"""FastAPI backend for the Policy Impact Scenario Mapper.

Endpoints:
    POST /api/analyze              start a run, return run_id
    GET  /api/runs/{run_id}        current status + intermediate graph state
    GET  /api/runs                 list recent run ids
    GET  /                         serve the SPA at static/index.html

Launch:
    uvicorn src.ui.app:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import threading
import traceback
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.orchestrator import PipelineResult, ProgressEvent, run_pipeline

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="policy-impact-scenario-mapper")

# In-memory run state. Populated by the background pipeline thread.
_RUN_STATE: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _serialize(obj: Any) -> Any:
    """Recursively convert dataclasses / dicts / lists to JSON-friendly form."""
    if obj is None:
        return None
    if is_dataclass(obj):
        return _serialize(asdict(obj))
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_serialize(v) for v in obj]
    if isinstance(obj, (int, float, str, bool)):
        return obj
    if hasattr(obj, "isoformat"):  # date / datetime
        return obj.isoformat()
    return str(obj)


def _new_run_id() -> str:
    return f"run_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}"


class AnalyzeRequest(BaseModel):
    event: str
    portfolio_context: str = ""
    max_first_order: int = 4
    max_analogs_per_node: int = 4
    similarity_threshold: float = 0.2
    use_moderator: bool = True
    run_scenarios: bool = True


class AnalyzeResponse(BaseModel):
    run_id: str
    status: str


def _on_progress_factory(run_id: str):
    def handler(ev: ProgressEvent) -> None:
        with _LOCK:
            state = _RUN_STATE.setdefault(run_id, _empty_state())
            state["events"].append({
                "kind": ev.kind,
                "message": ev.message,
                "ts": datetime.now().isoformat(timespec="seconds"),
            })
            data = ev.data or {}
            # Snapshot intermediate graphs as they fly past.
            if ev.kind == "stage_complete" and data.get("stage") == 1 and data.get("trunk"):
                state["trunk"] = _serialize(data["trunk"])
            elif ev.kind == "case_study_built" and data.get("subtree"):
                cs_id = data.get("case_study_id")
                if cs_id:
                    state["subtrees"].setdefault(cs_id, {})
                    state["subtrees"][cs_id].update({
                        "name": data.get("name"),
                        "first_order_label": data.get("first_order_label"),
                        "graph": _serialize(data["subtree"]),
                        "complete": True,
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    })
            elif ev.kind in ("subtree_init", "subtree_candidate_added", "subtree_candidate_merged") and data.get("partial_graph"):
                # Live partial subtree snapshot. Lets the UI render the tree
                # as it is being built, between case_study_started and case_study_built.
                cs_id = data.get("case_study_id")
                if cs_id:
                    state["subtrees"].setdefault(cs_id, {})
                    state["subtrees"][cs_id].update({
                        "name": data.get("name") or state["subtrees"][cs_id].get("name"),
                        "first_order_label": data.get("first_order_label") or state["subtrees"][cs_id].get("first_order_label"),
                        "graph": _serialize(data["partial_graph"]),
                        "complete": False,
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    })
            elif ev.kind == "merged_graph_built" and data.get("merged_graph"):
                state["merged_graph"] = _serialize(data["merged_graph"])
            elif ev.kind == "stage_complete" and data.get("stage") == 7 and data.get("pruned_graph"):
                state["pruned_graph"] = _serialize(data["pruned_graph"])
            # Live status text comes from the most recent stage_start.
            if ev.kind == "stage_start":
                state["current_stage"] = ev.message
    return handler


def _empty_state() -> dict[str, Any]:
    return {
        "status": {"state": "queued", "error": None},
        "events": [],
        "current_stage": None,
        "trunk": None,
        "subtrees": {},
        "merged_graph": None,
        "pruned_graph": None,
        "result": None,
        "request": None,
    }


def _run_in_thread(run_id: str, req: AnalyzeRequest) -> None:
    with _LOCK:
        state = _RUN_STATE.setdefault(run_id, _empty_state())
        state["status"] = {"state": "running", "error": None}
        state["request"] = req.model_dump()
    try:
        result: PipelineResult = run_pipeline(
            req.event,
            max_first_order=req.max_first_order,
            max_analogs_per_node=req.max_analogs_per_node,
            similarity_threshold=req.similarity_threshold,
            use_moderator=req.use_moderator,
            run_scenarios=req.run_scenarios,
            portfolio_context=req.portfolio_context,
            run_id=run_id,
            on_progress=_on_progress_factory(run_id),
        )
        with _LOCK:
            state = _RUN_STATE[run_id]
            state["status"] = {"state": "done", "error": None}
            state["result"] = _serialize_pipeline_result(result)
    except Exception as exc:
        with _LOCK:
            state = _RUN_STATE.setdefault(run_id, _empty_state())
            state["status"] = {
                "state": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }


def _serialize_pipeline_result(result: PipelineResult) -> dict[str, Any]:
    """Strip large fields from PipelineResult for JSON. Keeps graph + summaries."""
    return {
        "graph": _serialize(result.graph),
        "pre_prune_graph": _serialize(result.pre_prune_graph),
        "case_studies": [
            {
                "id": cs.id,
                "name": cs.name,
                "date_range": [cs.date_range[0].isoformat(), cs.date_range[1].isoformat()],
                "triggering_event": cs.triggering_event,
                "macro_snapshot": _serialize(cs.macro_snapshot),
                "similarity_score": cs.similarity_score,
                "subtree": _serialize(cs.subtree),
            }
            for cs in result.case_studies
        ],
        "portfolio_impacts": [_serialize(p) for p in result.portfolio_impacts],
        "debates": {
            tid: {
                "target_id": d.target_id,
                "critique": _serialize(d.critique),
                "rebuttal": _serialize(d.rebuttal),
                "verdict": _serialize(d.verdict),
                "survives": d.survives,
                "margin": d.margin,
            }
            for tid, d in result.debates.items()
        },
        "comparator_results": {k: _serialize(v) for k, v in result.comparator_results.items()},
        "case_study_to_first_order": result.case_study_to_first_order,
        "tail_scenarios": [_serialize(s) for s in result.tail_scenarios],
        "citation_validations": dict(result.citation_validations),
        "run_id": result.run_id,
    }


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest, background: BackgroundTasks) -> AnalyzeResponse:
    run_id = _new_run_id()
    with _LOCK:
        _RUN_STATE[run_id] = _empty_state()
    background.add_task(_run_in_thread, run_id, req)
    return AnalyzeResponse(run_id=run_id, status="queued")


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> JSONResponse:
    with _LOCK:
        state = _RUN_STATE.get(run_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"run_id {run_id} not found")
        # Return a shallow copy so the lock can be released.
        snapshot = {
            "run_id": run_id,
            "status": dict(state["status"]),
            "events": list(state["events"]),
            "current_stage": state["current_stage"],
            "trunk": state["trunk"],
            "subtrees": dict(state["subtrees"]),
            "merged_graph": state["merged_graph"],
            "pruned_graph": state["pruned_graph"],
            "result": state["result"],
        }
    return JSONResponse(snapshot)


@app.get("/api/runs")
def list_runs() -> dict[str, list[dict[str, Any]]]:
    with _LOCK:
        runs = []
        for rid, state in sorted(_RUN_STATE.items(), reverse=True):
            req = state.get("request") or {}
            runs.append({
                "run_id": rid,
                "state": state["status"]["state"],
                "event": (req.get("event") or "")[:120],
                "n_events": len(state["events"]),
                "has_result": state["result"] is not None,
            })
    return {"runs": runs[:50]}


# Static SPA mount — must come last so /api/* routes take precedence.
if STATIC_DIR.exists():
    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        idx = STATIC_DIR / "index.html"
        if not idx.exists():
            return HTMLResponse("<h1>UI missing</h1><p>static/index.html not found.</p>")
        return HTMLResponse(idx.read_text(encoding="utf-8"))

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
