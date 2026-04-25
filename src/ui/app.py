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
import json
import logging
import threading
import time
import traceback
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config import LOGS_DIR
from src.orchestrator import PipelineResult, ProgressEvent, run_pipeline

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
RUN_STATE_FILENAME = "state.json"
SAVE_DEBOUNCE_SEC = 1.0      # at most one save per run per second during a run
LOAD_MAX_RUNS = 100          # cap on how many on-disk runs to load at startup

app = FastAPI(title="policy-impact-scenario-mapper")

# In-memory run state. Populated by the background pipeline thread; also
# rehydrated from disk at module import via _load_run_states().
_RUN_STATE: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
# Last save time per run, used to debounce disk writes during high-frequency
# progress event bursts.
_LAST_SAVE: dict[str, float] = {}
_SAVE_LOCK = threading.Lock()


def _save_run_state(run_id: str, *, force: bool = False) -> None:
    """Persist a run's state to ``logs/{run_id}/state.json`` (atomic write).

    Debounced to at most once per ``SAVE_DEBOUNCE_SEC`` per run during a run.
    `force=True` bypasses the debounce — used on terminal status transitions
    (done / failed) so the final state always lands on disk.
    """
    now = time.time()
    with _SAVE_LOCK:
        last = _LAST_SAVE.get(run_id, 0.0)
        if not force and (now - last) < SAVE_DEBOUNCE_SEC:
            return
        _LAST_SAVE[run_id] = now

    with _LOCK:
        state = _RUN_STATE.get(run_id)
        if state is None:
            return
        snapshot = {
            "run_id": run_id,
            "status": state.get("status"),
            "events": list(state.get("events") or []),
            "current_stage": state.get("current_stage"),
            "trunk": state.get("trunk"),
            "subtrees": dict(state.get("subtrees") or {}),
            "merged_graph": state.get("merged_graph"),
            "pruned_graph": state.get("pruned_graph"),
            "consensus_graph": state.get("consensus_graph"),
            "result": state.get("result"),
            "request": state.get("request"),
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }

    out_path = LOGS_DIR / run_id / RUN_STATE_FILENAME
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(snapshot, f, default=str)
        tmp_path.replace(out_path)
    except Exception as exc:
        logger.warning("failed to persist state for run %s: %s", run_id, exc)


def _load_run_states(max_runs: int = LOAD_MAX_RUNS) -> int:
    """Load on-disk run states into ``_RUN_STATE`` at module import.

    Loads the ``max_runs`` most-recently-modified runs. Runs that were marked
    ``running`` on disk are flipped to ``failed`` with a "server restarted
    mid-run" error since their orchestrator thread didn't survive the restart.
    """
    if not LOGS_DIR.exists():
        return 0
    candidates: list[tuple[float, str, Path]] = []
    for d in LOGS_DIR.iterdir():
        if not d.is_dir():
            continue
        sf = d / RUN_STATE_FILENAME
        if not sf.exists():
            continue
        try:
            candidates.append((sf.stat().st_mtime, d.name, sf))
        except Exception:
            continue
    candidates.sort(key=lambda x: x[0], reverse=True)
    candidates = candidates[:max_runs]

    loaded = 0
    for _, rid, sf in candidates:
        try:
            with sf.open("r", encoding="utf-8") as f:
                snap = json.load(f)
        except Exception as exc:
            logger.warning("failed to load run state %s: %s", sf, exc)
            continue
        status = snap.get("status") or {"state": "unknown"}
        if status.get("state") in ("running", "queued"):
            status = {"state": "failed", "error": "server restarted mid-run"}
        with _LOCK:
            _RUN_STATE[rid] = {
                "status": status,
                "events": snap.get("events") or [],
                "current_stage": snap.get("current_stage"),
                "trunk": snap.get("trunk"),
                "subtrees": snap.get("subtrees") or {},
                "merged_graph": snap.get("merged_graph"),
                "pruned_graph": snap.get("pruned_graph"),
                "consensus_graph": snap.get("consensus_graph"),
                "result": snap.get("result"),
                "request": snap.get("request"),
            }
        loaded += 1
    if loaded:
        logger.info("loaded %d previous run states from %s", loaded, LOGS_DIR)
    return loaded


# Rehydrate any on-disk runs at module import so they appear in /api/runs
# immediately. Failures are logged and ignored — a corrupt state.json should
# not prevent the app from serving fresh requests.
try:
    _load_run_states()
except Exception as exc:
    logger.warning("run-state rehydration failed: %s", exc)


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
        # Persist after each event so a server crash mid-run loses at most one
        # event's worth of state. Debounced inside _save_run_state.
        _persist = True
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
            elif ev.kind == "case_study_started":
                # Pre-seed the cell's metadata (name, date_range) so the multi-grid
                # can show it before any subtree nodes have been built.
                cs_id = data.get("case_study_id")
                if cs_id:
                    state["subtrees"].setdefault(cs_id, {})
                    update = {"name": data.get("name") or state["subtrees"][cs_id].get("name")}
                    if data.get("date_range"):
                        update["date_range"] = data["date_range"]
                    state["subtrees"][cs_id].update(update)
            elif ev.kind == "case_study_built" and data.get("subtree"):
                cs_id = data.get("case_study_id")
                if cs_id:
                    state["subtrees"].setdefault(cs_id, {})
                    update = {
                        "name": data.get("name"),
                        "first_order_label": data.get("first_order_label"),
                        "graph": _serialize(data["subtree"]),
                        "complete": True,
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    }
                    if data.get("date_range"):
                        update["date_range"] = data["date_range"]
                    state["subtrees"][cs_id].update(update)
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
            elif ev.kind == "subtree_finalized" and data.get("subtree"):
                # Post-stage-7 reconciled view. Replaces the per-build snapshot
                # with one that reflects bridges/edges dropped during merge,
                # chain verify, and prune. Without this the multi-grid keeps
                # showing pre-prune subtrees long after the merged graph moved on.
                cs_id = data.get("case_study_id")
                if cs_id:
                    state["subtrees"].setdefault(cs_id, {})
                    update = {
                        "name": data.get("name") or state["subtrees"][cs_id].get("name"),
                        "first_order_label": state["subtrees"][cs_id].get("first_order_label"),
                        "graph": _serialize(data["subtree"]),
                        "complete": True,
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    }
                    if data.get("date_range"):
                        update["date_range"] = data["date_range"]
                    state["subtrees"][cs_id].update(update)
            elif ev.kind in ("subtree_dropped_finalize", "subtree_skipped"):
                # Case study was dropped pre-merge (similarity/applies-today)
                # or post-merge (no nodes survived). Either way it has no
                # business in the multi-grid — remove it.
                cs_id = data.get("case_study_id")
                if cs_id and cs_id in state["subtrees"]:
                    del state["subtrees"][cs_id]
            elif ev.kind == "subtree_dropped":
                # Pruner-level drop. The pruner emits the cs id under the
                # `case_study` key (not `case_study_id`), keep both for safety.
                cs_id = data.get("case_study_id") or data.get("case_study")
                if cs_id and cs_id in state["subtrees"]:
                    del state["subtrees"][cs_id]
            elif ev.kind == "merged_graph_built" and data.get("merged_graph"):
                state["merged_graph"] = _serialize(data["merged_graph"])
            elif ev.kind == "stage_complete" and data.get("stage") == 7 and data.get("pruned_graph"):
                state["pruned_graph"] = _serialize(data["pruned_graph"])
            elif ev.kind == "consensus_built" and data.get("consensus_graph"):
                state["consensus_graph"] = _serialize(data["consensus_graph"])
            # Live status text comes from the most recent stage_start.
            if ev.kind == "stage_start":
                state["current_stage"] = ev.message
        # Persist outside the lock to keep hold time short.
        if _persist:
            _save_run_state(run_id)
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
        "consensus_graph": None,
        "result": None,
        "request": None,
    }


def _run_in_thread(run_id: str, req: AnalyzeRequest) -> None:
    with _LOCK:
        state = _RUN_STATE.setdefault(run_id, _empty_state())
        state["status"] = {"state": "running", "error": None}
        state["request"] = req.model_dump()
    _save_run_state(run_id, force=True)
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
            # Drop any subtree entries that never finalized. A complete=False
            # entry after the pipeline has finished means its build raised
            # before case_study_built emitted, leaving a stale partial_graph
            # snapshot that would otherwise sit alongside the real subtrees in
            # the multi-grid (duplicated case study cards bug).
            state["subtrees"] = {
                cs_id: info
                for cs_id, info in (state.get("subtrees") or {}).items()
                if info.get("complete")
            }
        _save_run_state(run_id, force=True)
    except Exception as exc:
        with _LOCK:
            state = _RUN_STATE.setdefault(run_id, _empty_state())
            state["status"] = {
                "state": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        _save_run_state(run_id, force=True)


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
        "case_study_to_first_order_ids": dict(result.case_study_to_first_order_ids),
        "tail_scenarios": [_serialize(s) for s in result.tail_scenarios],
        "citation_validations": dict(result.citation_validations),
        "link_applicabilities": {k: _serialize(v) for k, v in result.link_applicabilities.items()},
        "consensus_graph": _serialize(result.consensus_graph) if result.consensus_graph else None,
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
            "consensus_graph": state.get("consensus_graph"),
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
