from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any, Optional

from src.agents._common import extract_json
from src.config import ANTHROPIC_API_KEY, LOGS_DIR, MODEL_FAST, PROMPTS_DIR
from src.types import Episode, Node, ToolBundle, ToolError

logger = logging.getLogger(__name__)

PROMPT_PATH = PROMPTS_DIR / "analog_search.md"

DEFAULT_K = 4
DEFAULT_DEDUP_DAYS = 180
DEFAULT_LOOKBACK_YEARS = 40
DEFAULT_THRESHOLD_Z = 2.0
DEFAULT_WINDOW_OBS = 60
DEFAULT_HISTORY_START = date(1980, 1, 1)


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _client_or_default(client: Any) -> Any:
    if client is not None:
        return client
    import anthropic

    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _log(run_id: Optional[str], stage: str, payload: dict[str, Any]) -> None:
    if not run_id:
        return
    log_dir = LOGS_DIR / run_id / "analog_search"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "calls.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"stage": stage, **payload}, default=str) + "\n")


def _format_node(node: Node) -> str:
    return (
        f"Node id: {node.id}\n"
        f"Node label: {node.label}\n"
        f"Description: {node.description}\n"
        f"Layer: {node.layer}"
    )


def _plan_series(
    node: Node, *, model: str, client: Any, run_id: Optional[str]
) -> Optional[dict[str, Any]]:
    user = "PLAN_SERIES\n" + _format_node(node)
    msg = client.messages.create(
        model=model,
        max_tokens=512,
        system=_load_prompt(),
        messages=[{"role": "user", "content": user}],
    )
    text = msg.content[0].text if msg.content else ""
    parsed = extract_json(text)
    _log(run_id, "plan_series", {"node_id": node.id, "raw": text, "parsed": parsed})
    if not isinstance(parsed, dict):
        return None
    if not parsed.get("primary_series"):
        return None
    return parsed


def _filter_direction(episodes: list[Episode], direction: str) -> list[Episode]:
    if direction == "up":
        return [e for e in episodes if e.magnitude > 0]
    if direction == "down":
        return [e for e in episodes if e.magnitude < 0]
    return list(episodes)


def _dedupe(episodes: list[Episode], *, window_days: int) -> list[Episode]:
    """Drop episodes whose start is within `window_days` of an already-kept
    episode on the same series. Assumes input is ordered so the first occurrence
    is the one to keep (typically sorted by abs(magnitude) desc)."""
    kept: list[Episode] = []
    gap = timedelta(days=window_days)
    for ep in episodes:
        clash = any(
            ep.series_id == k.series_id and abs(ep.start - k.start) <= gap
            for k in kept
        )
        if not clash:
            kept.append(ep)
    return kept


def _label_episodes(
    node: Node,
    episodes: list[Episode],
    *,
    model: str,
    client: Any,
    run_id: Optional[str],
) -> list[Optional[str]]:
    if not episodes:
        return []
    lines = [
        f"{i+1}. series_id={ep.series_id} start={ep.start} end={ep.end} "
        f"magnitude={ep.magnitude:.2f}"
        for i, ep in enumerate(episodes)
    ]
    user = (
        "LABEL_EPISODES\n"
        f"Node label: {node.label}\n"
        f"Description: {node.description}\n\n"
        "Episodes:\n" + "\n".join(lines)
    )
    msg = client.messages.create(
        model=model,
        max_tokens=1024,
        system=_load_prompt(),
        messages=[{"role": "user", "content": user}],
    )
    text = msg.content[0].text if msg.content else ""
    parsed = extract_json(text)
    _log(run_id, "label_episodes", {"node_id": node.id, "raw": text, "parsed": parsed})
    if not isinstance(parsed, dict):
        return [None] * len(episodes)
    items = parsed.get("episodes")
    if not isinstance(items, list):
        return [None] * len(episodes)

    out: list[Optional[str]] = []
    for i, _ in enumerate(episodes):
        if i < len(items) and isinstance(items[i], dict):
            label = items[i].get("candidate_event")
            out.append(str(label).strip() if label else None)
        else:
            out.append(None)
    return out


def run(
    node: Node,
    *,
    tools: ToolBundle,
    model: str = MODEL_FAST,
    client: Any = None,
    k: int = DEFAULT_K,
    history_start: Optional[date] = None,
    history_end: Optional[date] = None,
    dedup_window_days: int = DEFAULT_DEDUP_DAYS,
    run_id: Optional[str] = None,
) -> list[Episode]:
    """Find up to `k` historical Episodes where the Node's variable moved comparably.

    Three stages: (1) LLM picks a FRED series and spike-detection params,
    (2) deterministic spike detection via `tools.fred.fred_find_extrema`,
    (3) LLM labels each surviving Episode's `candidate_event`. Returns `[]` on
    any unrecoverable error; errors are logged via `run_id` but never raised."""
    if tools is None or tools.fred is None:
        logger.warning("analog_search.run called without a FRED tool")
        return []

    client = _client_or_default(client)

    plan = _plan_series(node, model=model, client=client, run_id=run_id)
    if plan is None:
        return []

    series_id = plan["primary_series"]
    threshold_z = float(plan.get("threshold_zscore") or DEFAULT_THRESHOLD_Z)
    window_obs = int(plan.get("window_obs") or DEFAULT_WINDOW_OBS)
    lookback_years = int(plan.get("lookback_years") or DEFAULT_LOOKBACK_YEARS)
    direction = str(plan.get("direction") or "either").lower()

    end = history_end or date.today()
    start = history_start or max(
        DEFAULT_HISTORY_START, end - timedelta(days=lookback_years * 365)
    )

    raw = tools.fred.fred_find_extrema(
        series_id,
        threshold_z,
        window_obs,
        history_start=start,
        history_end=end,
    )
    if isinstance(raw, ToolError):
        _log(run_id, "tool_error", {"node_id": node.id, "tool_error": raw.message})
        return []
    if not raw:
        return []

    filtered = _filter_direction(raw, direction)
    if not filtered:
        return []

    ranked = sorted(filtered, key=lambda e: abs(e.magnitude), reverse=True)
    deduped = _dedupe(ranked, window_days=dedup_window_days)
    top = deduped[:k]
    if not top:
        return []

    labels = _label_episodes(node, top, model=model, client=client, run_id=run_id)
    for ep, lbl in zip(top, labels):
        ep.candidate_event = lbl

    return top
