from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

from src.config import LOGS_DIR, MODEL, PROMPTS_DIR
from src.types import CaseStudy, Edge, Evidence, Node, ToolBundle, ToolError

# LogicCheck callable: receives (parent, candidate, mechanism) and returns
# {"ok": bool, "reason": str} or None if the check could not be run.
LogicCheck = Callable[[Node, Node, str], Optional[dict]]

# Confidence floor when LogicVerifier passes — out-of-sample primary signal.
LOGIC_PASS_FLOOR = 0.5
# Confidence cap when LogicVerifier fails — structural problem with the chain.
LOGIC_FAIL_CAP = 0.2

logger = logging.getLogger(__name__)

PROMPT_PATH = PROMPTS_DIR / "sensitivity.md"

# Rubric thresholds. Mirrors CLAUDE.md "Sensitivity and confidence" section.
DROP_CONF_BELOW = 0.3
DROP_SENS_BELOW = 0.2
PRIORS_ONLY_CAP = 0.3


@dataclass
class EdgeScore:
    """Output of score_edge. The orchestrator uses these to build an Edge."""

    sensitivity: float
    confidence: float
    mechanism_refined: str
    supporting_data: list[Evidence] = field(default_factory=list)
    magnitude_estimate: Optional[float] = None
    keep: bool = True
    keep_reason: str = ""


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _call_model(prompt: str, *, model: str, system: str = "") -> str:
    """Call the Anthropic model. Tests monkeypatch this attribute."""
    from anthropic import Anthropic

    from src.config import ANTHROPIC_API_KEY

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text  # type: ignore[attr-defined]


def _parse_json(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(line for line in lines if not line.startswith("```"))
        raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        s = raw.find(open_ch)
        e = raw.rfind(close_ch)
        if s != -1 and e != -1 and e > s:
            try:
                return json.loads(raw[s : e + 1])
            except json.JSONDecodeError:
                continue
    return {}


def _log_call(run_id: Optional[str], stage: str, payload: dict[str, Any]) -> None:
    # TODO(integration): lift to a shared logger in src/logging.py
    if not run_id:
        return
    log_dir = LOGS_DIR / run_id / "sensitivity"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "calls.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"stage": stage, **payload}, default=str) + "\n")


def _propose_data_refs(
    parent: Node,
    candidate: Node,
    mechanism: str,
    *,
    model: str,
) -> dict[str, Any]:
    """Step 1. Ask the model which FRED series and tickers would move if this edge holds."""
    user = (
        "STEP 1: PROPOSE_DATA_REFS.\n"
        f"Parent node: {parent.label}.\n"
        f"Parent description: {parent.description}.\n"
        f"Candidate child: {candidate.label}.\n"
        f"Candidate description: {candidate.description}.\n"
        f"Proposed mechanism: {mechanism}.\n\n"
        "Propose 1 to 4 FRED series IDs and 0 to 3 equity or futures tickers that should move "
        "materially if this edge is real. Pick observable series, not abstractions. "
        "Respond with JSON only:\n"
        '{"fred_series": ["SERIES_ID", ...], "tickers": ["TICKER", ...], '
        '"reasoning": "one sentence"}'
    )
    raw = _call_model(user, model=model, system=_load_prompt())
    parsed = _parse_json(raw)
    if not isinstance(parsed, dict):
        return {"fred_series": [], "tickers": [], "reasoning": ""}
    return parsed


def _summarize_series(
    df: pd.DataFrame, t0: date, t1: date, value_col: Optional[str] = None
) -> dict[str, Any]:
    """Pre-mean, post-mean, peak deviation, peak z-score, time-to-peak.

    The agent cannot reason over raw time series. Hand it stats."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        return {"error": "empty dataframe"}
    if value_col and value_col in df.columns:
        col = value_col
    else:
        col = next(
            (c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])),
            None,
        )
    if col is None:
        return {"error": "no numeric column"}
    s = df[col]
    # Defensive: yfinance MultiIndex columns can leave df[col] as a 1-col DataFrame.
    if isinstance(s, pd.DataFrame):
        if s.shape[1] == 1:
            s = s.iloc[:, 0]
        else:
            return {"error": "ambiguous columns after value_col select"}
    s = s.dropna()
    if s.empty:
        return {"error": "all NaN"}
    s.index = pd.to_datetime(s.index)
    t0_ts = pd.Timestamp(t0)
    t1_ts = pd.Timestamp(t1)
    pre_mask = (s.index >= t0_ts - pd.Timedelta(days=30)) & (s.index < t0_ts)
    post_mask = (s.index >= t0_ts) & (s.index <= t1_ts)
    pre = s.loc[pre_mask]
    post = s.loc[post_mask]
    if pre.empty or post.empty:
        return {"error": "insufficient window data"}
    pre_mean = float(pre.mean())
    pre_std = float(pre.std()) if len(pre) > 1 else 0.0
    post_mean = float(post.mean())
    deviations = post - pre_mean
    idx = deviations.abs().idxmax()
    peak_dev = float(deviations.loc[idx])
    peak_z = peak_dev / pre_std if pre_std > 1e-9 else 0.0
    time_to_peak_days = int((idx - t0_ts).days)
    return {
        "pre_event_mean": round(pre_mean, 6),
        "post_event_mean": round(post_mean, 6),
        "peak_deviation": round(peak_dev, 6),
        "peak_z": round(peak_z, 4),
        "time_to_peak_days": time_to_peak_days,
        "n_pre": int(len(pre)),
        "n_post": int(len(post)),
    }


def _gather_evidence(
    parent: Node,
    candidate: Node,
    mechanism: str,
    case_study: CaseStudy,
    tools: ToolBundle,
    model: str,
) -> tuple[list[dict[str, Any]], list[Evidence]]:
    """Run step 1 plus tool calls. Return (summary stats list, Evidence list)."""
    refs = _propose_data_refs(parent, candidate, mechanism, model=model)
    fred_ids = [s for s in refs.get("fred_series", []) if isinstance(s, str)][:4]
    tickers = [t for t in refs.get("tickers", []) if isinstance(t, str)][:3]

    t0, t1 = case_study.date_range
    fetch_start = t0 - timedelta(days=30)
    fetch_end = t1 + timedelta(days=90)

    summaries: list[dict[str, Any]] = []
    evidence: list[Evidence] = []

    for sid in fred_ids:
        if tools.fred is None:
            evidence.append(Evidence(kind="fred_series", ref=sid, note="no fred tool bound"))
            continue
        try:
            result = tools.fred.fred_get_series(sid, fetch_start, fetch_end)
        except Exception as exc:
            evidence.append(
                Evidence(kind="fred_series", ref=sid, note=f"tool_error: {exc}")
            )
            continue
        if isinstance(result, ToolError):
            evidence.append(
                Evidence(kind="fred_series", ref=sid, note=f"tool_error: {result.message}")
            )
            continue
        stats = _summarize_series(result, t0, t1)
        if "error" in stats:
            evidence.append(Evidence(kind="fred_series", ref=sid, note=stats["error"]))
            continue
        summaries.append({"ref": sid, "kind": "fred_series", **stats})
        evidence.append(Evidence(kind="fred_series", ref=sid, payload=stats))

    for tk in tickers:
        if tools.yahoo is None:
            evidence.append(Evidence(kind="ticker", ref=tk, note="no yahoo tool bound"))
            continue
        try:
            result = tools.yahoo.yahoo_prices(tk, fetch_start, fetch_end)
        except Exception as exc:
            evidence.append(Evidence(kind="ticker", ref=tk, note=f"tool_error: {exc}"))
            continue
        if isinstance(result, ToolError):
            evidence.append(Evidence(kind="ticker", ref=tk, note=f"tool_error: {result.message}"))
            continue
        stats = _summarize_series(result, t0, t1, value_col="Close")
        if "error" in stats:
            evidence.append(Evidence(kind="ticker", ref=tk, note=stats["error"]))
            continue
        summaries.append({"ref": tk, "kind": "ticker", **stats})
        evidence.append(Evidence(kind="ticker", ref=tk, payload=stats))

    return summaries, evidence


def score_edge(
    parent: Node,
    candidate: Node,
    mechanism: str,
    case_study: CaseStudy,
    *,
    tools: ToolBundle,
    model: str = MODEL,
    logic_check: Optional[LogicCheck] = None,
    run_id: Optional[str] = None,
) -> EdgeScore:
    """Score an edge from `parent` to `candidate` within `case_study`.

    Two model calls: propose data refs, then score given summary stats.

    `logic_check`, when provided, is a callable that returns a `{"ok", "reason"}`
    dict (or None on failure). It is treated as the **primary** out-of-sample
    validity signal: logic-passing edges are floored at confidence 0.5 even
    without empirical data; logic-failing edges are capped at 0.2 and marked
    drop. When `logic_check` is None, falls back to PRIORS_ONLY_CAP behavior.
    """
    summaries, supporting = _gather_evidence(
        parent, candidate, mechanism, case_study, tools, model
    )

    user = (
        "STEP 2: SCORE_EDGE.\n"
        f"Case study: {case_study.name} "
        f"({case_study.date_range[0]} to {case_study.date_range[1]}).\n"
        f"Parent: {parent.label}. {parent.description}\n"
        f"Candidate child: {candidate.label}. {candidate.description}\n"
        f"Proposed mechanism: {mechanism}.\n"
        f"Summary stats from FRED/Yahoo over the window:\n"
        f"{json.dumps(summaries, indent=2, default=str)}\n\n"
        "Score this edge against the rubric in the system prompt. Cite the series IDs you used. "
        "Respond with JSON only:\n"
        "{"
        '"sensitivity": <0.0-1.0>, '
        '"confidence": <0.0-1.0>, '
        '"mechanism_refined": "one sentence", '
        '"supporting_data": [{"series_id": "...", "peak_z": <float>, "interpretation": "..."}], '
        '"magnitude_estimate": <signed float or null>, '
        '"keep": <bool>, '
        '"keep_reason": "one sentence"'
        "}"
    )
    raw = _call_model(user, model=model, system=_load_prompt())
    parsed = _parse_json(raw)
    if not isinstance(parsed, dict):
        parsed = {}

    sens = _coerce_unit(parsed.get("sensitivity"))
    conf = _coerce_unit(parsed.get("confidence"))
    mech_refined = str(parsed.get("mechanism_refined") or mechanism).strip() or mechanism
    mag = parsed.get("magnitude_estimate")
    if mag is not None:
        try:
            mag = float(mag)
        except (TypeError, ValueError):
            mag = None

    # Annotate Evidence with the model's interpretation when available.
    interp_by_ref: dict[str, str] = {}
    for item in parsed.get("supporting_data") or []:
        if not isinstance(item, dict):
            continue
        ref = item.get("series_id") or item.get("ref")
        if isinstance(ref, str):
            interp_by_ref[ref] = str(item.get("interpretation") or "")
    for ev in supporting:
        note_extra = interp_by_ref.get(ev.ref)
        if note_extra:
            ev.note = (ev.note + "; " + note_extra) if ev.note else note_extra

    # LogicVerifier as primary out-of-sample signal. Run it before the data
    # cap so logic alone can sustain confidence when empirical data is thin.
    logic_ok: Optional[bool] = None
    logic_reason: str = ""
    if logic_check is not None:
        try:
            result = logic_check(parent, candidate, mechanism)
        except Exception as exc:
            logger.warning("logic_check raised: %s", exc)
            result = None
        if isinstance(result, dict):
            logic_ok = bool(result.get("ok"))
            logic_reason = str(result.get("reason") or "")[:200]
            note = f"logic {'passed' if logic_ok else 'failed'}: {logic_reason}"
            supporting.append(
                Evidence(kind="logic_check", ref="logic_verifier", note=note)
            )

    has_usable_data = any(
        ev.payload for ev in supporting if ev.kind != "logic_check"
    )

    if logic_ok is True:
        # Logic passes: floor confidence at LOGIC_PASS_FLOOR. Empirical data
        # may have already pushed it higher; we never lower it here.
        if conf < LOGIC_PASS_FLOOR:
            conf = LOGIC_PASS_FLOOR
    elif logic_ok is False:
        # Logic fails: this is a structural problem. Cap confidence and force drop.
        if conf > LOGIC_FAIL_CAP:
            conf = LOGIC_FAIL_CAP
    elif not has_usable_data and conf > PRIORS_ONLY_CAP:
        # No logic check, no data: classic priors-only cap.
        conf = PRIORS_ONLY_CAP

    # Hard rubric: drop if confidence < 0.3 AND sensitivity < 0.2.
    rubric_keep = not (conf < DROP_CONF_BELOW and sens < DROP_SENS_BELOW)
    logic_keep = logic_ok is not False  # explicit False forces a drop
    model_keep = bool(parsed.get("keep", True))
    keep = rubric_keep and model_keep and logic_keep
    keep_reason = str(parsed.get("keep_reason") or "").strip()
    if not rubric_keep:
        keep_reason = (
            f"rubric drop: sensitivity={sens:.2f}, confidence={conf:.2f}. "
            + keep_reason
        ).strip()
    if not logic_keep:
        keep_reason = (
            f"logic check failed: {logic_reason}. " + keep_reason
        ).strip()

    score = EdgeScore(
        sensitivity=sens,
        confidence=conf,
        mechanism_refined=mech_refined,
        supporting_data=supporting,
        magnitude_estimate=mag if isinstance(mag, (int, float)) else None,
        keep=keep,
        keep_reason=keep_reason,
    )
    _log_call(
        run_id,
        "score_edge",
        {
            "parent": parent.id,
            "candidate": candidate.id,
            "mechanism": mechanism,
            "summaries": summaries,
            "raw_output": raw,
            "score": {
                "sensitivity": score.sensitivity,
                "confidence": score.confidence,
                "keep": score.keep,
                "keep_reason": score.keep_reason,
            },
        },
    )
    return score


def _coerce_unit(value: Any) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if f != f:  # NaN
        return 0.0
    return max(0.0, min(1.0, f))


def run(edge: Edge, *, tools: ToolBundle, model: str = MODEL) -> Edge:
    """Compatibility shim. Prefer score_edge for the full pipeline path.

    The bare Edge does not carry the parent/candidate Node descriptions or the
    case study date range that score_edge needs to ground its scoring."""
    raise NotImplementedError(
        "Use score_edge(parent, candidate, mechanism, case_study, ...). "
        "The bare Edge lacks the context required to ground a score."
    )
