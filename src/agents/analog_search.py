from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
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
DEFAULT_WEB_SEARCH_MAX_USES = 6
WEB_SOURCE_TAG = "WEB_SEARCH"  # Episode.series_id sentinel for web-found episodes


WEB_SYSTEM_PROMPT = """You are a macro events researcher. Given a downstream
variable description, find historical episodes (1980 to present) where a
comparable variable moved sharply. Use the web_search tool liberally to verify
dates, identify named events, and pull in recent episodes (post training
cutoff). Submit episodes via the submit_episodes tool.

Prioritize:
1. Named events with verifiable dates (Section 301 tariffs Jul 2018, LTRO Dec 2011, COVID March 2020).
2. Episodes from the last 24 months that may not be in static training data.
3. International episodes if the variable is non-US.

Each episode should be a real, dated, name-able historical event. Do not
fabricate. If you are unsure of a date, search to confirm. Return at most
2k episodes (caller will dedupe and rank)."""

SUBMIT_EPISODES_TOOL = {
    "name": "submit_episodes",
    "description": "Submit historical analog episodes after web research.",
    "input_schema": {
        "type": "object",
        "properties": {
            "episodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "ISO YYYY-MM-DD"},
                        "end_date": {"type": "string", "description": "ISO YYYY-MM-DD; same as start if a one-day event"},
                        "candidate_event": {"type": "string", "description": "Short named label, e.g. '2018 Section 301 tariffs'"},
                        "magnitude_z_estimate": {"type": "number", "description": "Rough peak z-score, signed; 2.0 default if unknown"},
                        "relevance": {"type": "string", "description": "One sentence on why this matches the variable"},
                    },
                    "required": ["start_date", "candidate_event"],
                },
            },
        },
        "required": ["episodes"],
    },
}


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


def _via_web_search(
    node: Node,
    *,
    model: str,
    client: Any,
    k: int,
    max_uses: int,
    run_id: Optional[str],
) -> list[Episode]:
    """Use Claude with the server-side web_search tool to find historical
    episodes for the given Node. Empty list on any failure."""
    user = (
        f'Variable to research: "{node.label}"\n'
        f"Description: {node.description}\n\n"
        f"Find up to {2 * k} historical episodes where a comparable variable "
        f"moved sharply. Verify dates with web_search. Submit via submit_episodes."
    )
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=4096,
            system=WEB_SYSTEM_PROMPT,
            tools=[
                {"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses},
                SUBMIT_EPISODES_TOOL,
            ],
            messages=[{"role": "user", "content": user}],
        )
    except Exception as exc:
        logger.warning("web search analog discovery failed: %s", exc)
        _log(run_id, "web_search_error", {"node_id": node.id, "error": str(exc)})
        return []

    raw_episodes: list[dict[str, Any]] = []
    for block in getattr(msg, "content", []) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", "") == "submit_episodes":
            input_data = getattr(block, "input", {}) or {}
            raw_episodes = input_data.get("episodes") or []
            break

    out: list[Episode] = []
    for raw in raw_episodes:
        if not isinstance(raw, dict):
            continue
        try:
            start = date.fromisoformat(str(raw.get("start_date", "")))
        except ValueError:
            continue
        end_raw = raw.get("end_date") or raw.get("start_date")
        try:
            end = date.fromisoformat(str(end_raw))
        except (TypeError, ValueError):
            end = start
        try:
            mag = float(raw.get("magnitude_z_estimate") or 2.0)
        except (TypeError, ValueError):
            mag = 2.0
        candidate_event = str(raw.get("candidate_event") or "").strip()
        if not candidate_event:
            continue
        out.append(
            Episode(
                series_id=WEB_SOURCE_TAG,
                start=start,
                end=end,
                magnitude=mag,
                candidate_event=candidate_event,
            )
        )
    _log(run_id, "web_search_complete", {"node_id": node.id, "n_episodes": len(out)})
    return out[: 2 * k]


def _via_fred(
    node: Node,
    *,
    tools: ToolBundle,
    model: str,
    client: Any,
    k: int,
    history_start: Optional[date],
    history_end: Optional[date],
    dedup_window_days: int,
    run_id: Optional[str],
) -> list[Episode]:
    """FRED extrema path. Plan series → spike scan → label."""
    if tools is None or tools.fred is None:
        logger.warning("FRED analog path called without a FRED tool")
        return []
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
        series_id, threshold_z, window_obs,
        history_start=start, history_end=end,
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
        return top
    labels = _label_episodes(node, top, model=model, client=client, run_id=run_id)
    for ep, lbl in zip(top, labels):
        ep.candidate_event = lbl
    return top


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
    enable_web_search: bool = True,
    web_search_max_uses: int = DEFAULT_WEB_SEARCH_MAX_USES,
    run_id: Optional[str] = None,
) -> list[Episode]:
    """Find up to `k` historical Episodes for analog seeding.

    Two parallel paths:
    1. **FRED extrema** (deterministic): plan a FRED series, scan for sharp
       moves, label each via LLM.
    2. **Web search** (LLM with web_search tool): the model researches recent
       and out-of-sample episodes that don't have a clean single-FRED-series
       signature, then submits structured episodes.

    Both paths run concurrently. Results are merged, deduped, ranked by
    abs(magnitude), and capped at `k`. Web episodes carry
    ``series_id = "WEB_SEARCH"`` so downstream consumers can tell them apart.
    """
    client = _client_or_default(client)

    fred_eps: list[Episode] = []
    web_eps: list[Episode] = []

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures: list[tuple[str, Any]] = []
        if tools is not None and tools.fred is not None:
            futures.append(("fred", executor.submit(
                _via_fred,
                node, tools=tools, model=model, client=client, k=k,
                history_start=history_start, history_end=history_end,
                dedup_window_days=dedup_window_days, run_id=run_id,
            )))
        else:
            logger.warning("analog_search.run called without a FRED tool")
        if enable_web_search:
            futures.append(("web", executor.submit(
                _via_web_search,
                node, model=model, client=client, k=k,
                max_uses=web_search_max_uses, run_id=run_id,
            )))
        for tag, fut in futures:
            try:
                result = fut.result()
            except Exception as exc:
                logger.warning("analog %s path failed: %s", tag, exc)
                continue
            if tag == "fred":
                fred_eps = result or []
            elif tag == "web":
                web_eps = result or []

    combined = fred_eps + web_eps
    if not combined:
        return []
    combined.sort(key=lambda e: abs(e.magnitude or 1.0), reverse=True)
    deduped = _dedupe(combined, window_days=dedup_window_days)
    return deduped[:k]
