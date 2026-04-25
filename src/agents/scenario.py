from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from src.config import PROMPTS_DIR
from src.tools.cache import disk_cache
from src.types import ToolBundle, ToolError

KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

# TODO(integration): HF news access duplicates what `tools/hf.py::news_search`
# is meant to provide. When the tools team wires it, swap _hf_news_search out
# for tools.news and delete the local helpers.
HF_NEWS_REPO = "BridgewaterAIHackathon/BW-AI-Hackathon"
HF_NEWS_PARQUET = (
    "Unstructured_Data/Macro/FinancialNewsAndCentralBanksSpeeches-Summary-Rag/"
    "train-00000-of-00001.parquet"
)

# Kalshi groups markets under events, events under series. Categories live on
# the event record. The /events query string `category` filter is silently
# ignored, so we paginate and filter client side.
POLICY_CATEGORIES: frozenset[str] = frozenset(
    {"Financials", "Politics", "World", "Elections", "Companies"}
)


@dataclass
class KalshiMarket:
    ticker: str
    event_ticker: str
    title: str
    yes_price: Optional[float]  # in [0, 1]
    yes_bid: Optional[float] = None
    yes_ask: Optional[float] = None
    volume_24h: Optional[float] = None
    close_time: Optional[str] = None
    yes_sub_title: Optional[str] = None
    no_sub_title: Optional[str] = None


@dataclass
class KalshiEvent:
    event_ticker: str
    title: str
    category: Optional[str] = None
    sub_title: Optional[str] = None


@dataclass
class NewsItem:
    id: str  # row index in the underlying corpus
    text: str
    date: Optional[str] = None  # ISO date if parseable from the prefix
    source: Optional[str] = None  # FED, ECB, etc.


PROBABILITY_SOURCES = ("kalshi_exact", "kalshi_adjusted", "llm_calibrated")
POLICY_AXES = ("monetary", "trade", "fiscal", "geopolitical", "regulatory")
ANCHOR_DELTA_CAP = 0.15  # max |probability - kalshi_anchor_price| for kalshi_adjusted


@dataclass
class TailScenario:
    text: str
    probability: float
    probability_source: str = "llm_calibrated"
    kalshi_market_ticker: Optional[str] = None
    kalshi_anchor_price: Optional[float] = None  # verbatim from API, never modified
    delta_rationale: Optional[str] = None  # required when source is kalshi_adjusted
    news_citations: list[str] = field(default_factory=list)
    policy_axis: Optional[str] = None
    time_horizon_days: int = 90
    feedback_event: str = ""
    rationale: Optional[str] = None  # base rate plus adjustment, for the audit trail

    def __post_init__(self) -> None:
        if not self.feedback_event:
            self.feedback_event = self.text


def _load_prompt() -> str:
    return (PROMPTS_DIR / "scenario.md").read_text(encoding="utf-8")


def _parse_dollar(v: Any) -> Optional[float]:
    """Kalshi returns prices as decimal strings in dollars, already in [0, 1]."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _kalshi_get(path: str, **params: Any) -> dict[str, Any]:
    """GET against Kalshi's public REST API with simple 429 backoff."""
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{KALSHI_BASE_URL}{path}?{qs}" if qs else f"{KALSHI_BASE_URL}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    delay = 1.0
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError("kalshi: exhausted retries")


@disk_cache("kalshi_events")
def _kalshi_fetch_events(
    status: str = "open",
    max_pages: int = 5,
    page_size: int = 200,
) -> list[dict[str, Any]]:
    """Walk /events with cursor pagination. Cached on disk."""
    out: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    for _ in range(max_pages):
        page = _kalshi_get("/events", status=status, limit=page_size, cursor=cursor)
        out.extend(page.get("events", []) or [])
        cursor = page.get("cursor")
        if not cursor:
            break
    return out


@disk_cache("kalshi_markets_for_event")
def _kalshi_markets_for_event(event_ticker: str) -> list[dict[str, Any]]:
    page = _kalshi_get("/markets", event_ticker=event_ticker, status="open", limit=200)
    return page.get("markets", []) or []


def _market_from_raw(m: dict[str, Any]) -> KalshiMarket:
    return KalshiMarket(
        ticker=m.get("ticker", ""),
        event_ticker=m.get("event_ticker", ""),
        title=m.get("title", ""),
        yes_price=_parse_dollar(m.get("last_price_dollars")),
        yes_bid=_parse_dollar(m.get("yes_bid_dollars")),
        yes_ask=_parse_dollar(m.get("yes_ask_dollars")),
        volume_24h=_parse_dollar(m.get("volume_24h_fp")),
        close_time=m.get("close_time"),
        yes_sub_title=m.get("yes_sub_title"),
        no_sub_title=m.get("no_sub_title"),
    )


# --- HF news corpus access ---------------------------------------------------

_NEWS_DF: Any = None  # lazy loaded pandas DataFrame
_NEWS_META_RE = re.compile(r"^On (\d{4}-\d{2}-\d{2}) the ([A-Z][A-Za-z]*) delivered")


def _load_news_df() -> Any:
    """One time load of the BW FinancialNews+Speeches corpus. Cached on disk by HF Hub."""
    global _NEWS_DF
    if _NEWS_DF is not None:
        return _NEWS_DF
    import pandas as pd
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(HF_NEWS_REPO, HF_NEWS_PARQUET, repo_type="dataset")
    _NEWS_DF = pd.read_parquet(path)
    return _NEWS_DF


def _parse_news_meta(text: str) -> tuple[Optional[str], Optional[str]]:
    m = _NEWS_META_RE.match(text)
    if m:
        return m.group(1), m.group(2)
    return None, None


def hf_news_search(
    query: str,
    *,
    limit: int = 30,
    since: Optional[str] = None,
    snippet_chars: int = 1500,
) -> list[NewsItem] | ToolError:
    """Keyword AND search over the BW FinancialNews+Speeches summarized corpus.

    Each non trivial term must appear (case insensitive) in the row text.

    Args:
        query: free text. Terms shorter than 3 chars are dropped.
        limit: max items to return.
        since: optional ISO date 'YYYY-MM-DD'. Items before this date are skipped.
        snippet_chars: truncate row text to this many chars before returning.
    """
    try:
        df = _load_news_df()
    except Exception as e:  # noqa: BLE001 surface failure
        return ToolError(
            tool="hf_news_search",
            args={"query": query, "limit": limit, "since": since},
            message=f"{type(e).__name__}: {e}",
        )

    terms = [t for t in query.lower().split() if len(t) >= 3]
    if not terms:
        return []

    text_lower = df["text"].str.lower()
    mask = text_lower.str.contains(terms[0], regex=False, na=False)
    for t in terms[1:]:
        mask &= text_lower.str.contains(t, regex=False, na=False)

    out: list[NewsItem] = []
    for idx, text in df.loc[mask, "text"].items():
        date, source = _parse_news_meta(text)
        if since and date and date < since:
            continue
        out.append(
            NewsItem(
                id=str(idx),
                text=text[:snippet_chars],
                date=date,
                source=source,
            )
        )
        if len(out) >= limit:
            break
    return out


def _is_meaningful_market(m: KalshiMarket) -> bool:
    """A market is meaningful only if it carries a non degenerate, traded price.

    Drops:
    - markets with no yes_price (never quoted)
    - markets at 0.0 or 1.0 (effectively settled, no remaining tail)
    - markets with zero 24h volume (quoted but not actively traded)
    """
    if m.yes_price is None:
        return False
    if m.yes_price <= 0.0 or m.yes_price >= 1.0:
        return False
    if m.volume_24h is None or m.volume_24h <= 0.0:
        return False
    return True


def kalshi_policy_search(
    query: str,
    *,
    categories: frozenset[str] = POLICY_CATEGORIES,
    max_events: int = 20,
) -> list[KalshiMarket] | ToolError:
    """Find policy relevant Kalshi markets matching a free text query.

    Walks open events, filters client side by category + keyword, then fetches
    markets for each matching event. Returns a flat list with parsed prices.
    Only meaningful markets (traded, non degenerate price, non zero volume)
    are returned, so callers do not need to filter again.
    """
    try:
        events = _kalshi_fetch_events(status="open")
    except Exception as e:  # noqa: BLE001 surface the failure
        return ToolError(
            tool="kalshi_policy_search",
            args={"query": query},
            message=f"{type(e).__name__}: {e}",
        )

    terms = [t for t in query.lower().split() if len(t) >= 3]
    matched_events: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("category") not in categories:
            continue
        haystack = f"{ev.get('title', '')} {ev.get('sub_title', '')}".lower()
        if not terms or any(t in haystack for t in terms):
            matched_events.append(ev)
        if len(matched_events) >= max_events:
            break

    out: list[KalshiMarket] = []
    for ev in matched_events:
        try:
            raw_markets = _kalshi_markets_for_event(ev["event_ticker"])
        except Exception:  # noqa: BLE001 skip the event, do not invent data
            continue
        for raw in raw_markets:
            m = _market_from_raw(raw)
            if _is_meaningful_market(m):
                out.append(m)
    return out


SUBMIT_TOOL = {
    "name": "submit_tail_scenarios",
    "description": "Submit the final list of tail policy scenarios.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scenarios": {
                "type": "array",
                "minItems": 4,
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "probability": {"type": "number", "minimum": 0.02, "maximum": 0.5},
                        "probability_source": {"type": "string", "enum": list(PROBABILITY_SOURCES)},
                        "kalshi_market_ticker": {"type": ["string", "null"]},
                        "kalshi_anchor_price": {"type": ["number", "null"]},
                        "delta_rationale": {"type": ["string", "null"]},
                        "news_citations": {"type": "array", "items": {"type": "string"}},
                        "policy_axis": {"type": "string", "enum": list(POLICY_AXES)},
                        "time_horizon_days": {"type": "integer", "minimum": 30, "maximum": 365},
                        "feedback_event": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                    "required": [
                        "text",
                        "probability",
                        "probability_source",
                        "policy_axis",
                        "time_horizon_days",
                        "feedback_event",
                        "rationale",
                    ],
                },
            }
        },
        "required": ["scenarios"],
    },
}


def _post_process(
    raw_scenarios: list[dict[str, Any]],
    *,
    kalshi_by_ticker: dict[str, KalshiMarket],
    valid_news_ids: set[str],
) -> list[TailScenario]:
    """Enforce the kalshi anchor and citation contracts. Drop invalid items."""
    out: list[TailScenario] = []
    for s in raw_scenarios:
        ticker = s.get("kalshi_market_ticker")
        source = s.get("probability_source", "llm_calibrated")
        prob = float(s.get("probability", 0.0))
        anchor = None
        delta_rationale = s.get("delta_rationale")

        # Kalshi anchor enforcement: live yes_price is the source of truth.
        if ticker and ticker in kalshi_by_ticker:
            anchor = kalshi_by_ticker[ticker].yes_price
            if source == "kalshi_exact" and anchor is not None:
                prob = anchor  # overwrite, no judgment allowed for exact
                delta_rationale = None
            elif source == "kalshi_adjusted" and anchor is not None:
                # Constraints: bounded delta, same side of 0.5, rationale required
                same_side = (prob >= 0.5) == (anchor >= 0.5)
                in_band = abs(prob - anchor) <= ANCHOR_DELTA_CAP
                has_rationale = bool(delta_rationale and delta_rationale.strip())
                if not (same_side and in_band and has_rationale):
                    prob = anchor  # constraint violation, fall back to overwrite
                    source = "kalshi_exact"
                    delta_rationale = None
        elif ticker:
            # Model invented a ticker. Strip the kalshi claims, keep as llm_calibrated.
            ticker = None
            source = "llm_calibrated"
            anchor = None
            delta_rationale = None

        if source == "llm_calibrated":
            ticker = None
            anchor = None
            delta_rationale = None

        # Coerce news citations to ids that actually exist in the corpus we sent.
        cites = [c for c in (s.get("news_citations") or []) if c in valid_news_ids]

        out.append(
            TailScenario(
                text=s.get("text", ""),
                probability=max(0.02, min(0.5, prob)),
                probability_source=source,
                kalshi_market_ticker=ticker,
                kalshi_anchor_price=anchor,
                delta_rationale=delta_rationale,
                news_citations=cites,
                policy_axis=s.get("policy_axis"),
                time_horizon_days=int(s.get("time_horizon_days", 90)),
                feedback_event=s.get("feedback_event") or s.get("text", ""),
                rationale=s.get("rationale"),
            )
        )
    return out


def _build_user_message(
    seed_event: str,
    markets: list[KalshiMarket],
    news: list[NewsItem],
) -> str:
    """Compact JSON payload for the user message."""
    return json.dumps(
        {
            "seed_event": seed_event,
            "kalshi_markets": [
                {
                    "ticker": m.ticker,
                    "event_ticker": m.event_ticker,
                    "title": m.title,
                    "yes_price": m.yes_price,
                    "yes_sub_title": m.yes_sub_title,
                    "close_time": m.close_time,
                }
                for m in markets
                if m.yes_price is not None
            ],
            "news_items": [
                {"id": n.id, "date": n.date, "source": n.source, "text": n.text}
                for n in news
            ],
        },
        ensure_ascii=False,
    )


def _log_call(payload: dict[str, Any]) -> None:
    """Append one JSON line to logs/scenario.log. Cheap, append only."""
    from src.config import LOGS_DIR

    log_path = LOGS_DIR / "scenario.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")


def run(
    seed_event: str,
    *,
    tools: ToolBundle,
    model: str,
    news_corpus: Optional[list[dict[str, Any]]] = None,
    kalshi_query: Optional[str] = None,
    client: Any = None,  # injectable for tests
) -> list[TailScenario]:
    """Generate tail policy scenarios for a seed event.

    See prompts/scenario.md for the full contract. Pulls live Kalshi markets
    and the BW HF news corpus, asks Claude to propose 4 to 8 tail scenarios,
    then enforces the anchor and citation contracts before returning.
    """
    # 1. Pull Kalshi anchors (current open markets)
    kalshi_raw = kalshi_policy_search(kalshi_query or seed_event)
    markets: list[KalshiMarket] = kalshi_raw if isinstance(kalshi_raw, list) else []
    kalshi_by_ticker = {m.ticker: m for m in markets if m.ticker}

    # 2. Pull news
    news: list[NewsItem]
    if news_corpus is not None:
        news = [
            NewsItem(
                id=str(n.get("id", i)),
                text=str(n.get("text", "")),
                date=n.get("date"),
                source=n.get("source"),
            )
            for i, n in enumerate(news_corpus)
        ]
    else:
        news_raw = hf_news_search(seed_event, limit=25)
        news = news_raw if isinstance(news_raw, list) else []
    valid_news_ids = {n.id for n in news}

    # 3. Build prompt and call Claude with forced tool use
    if client is None:
        from anthropic import Anthropic

        client = Anthropic()  # reads ANTHROPIC_API_KEY from env

    system_prompt = _load_prompt()
    user_message = _build_user_message(seed_event, markets, news)

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[SUBMIT_TOOL],
        tool_choice={"type": "tool", "name": "submit_tail_scenarios"},
        messages=[{"role": "user", "content": user_message}],
    )

    # 4. Extract the tool call payload
    raw_scenarios: list[dict[str, Any]] = []
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_tail_scenarios":
            raw_scenarios = block.input.get("scenarios", [])
            break

    # 5. Post process: enforce anchor + citation contracts
    scenarios = _post_process(
        raw_scenarios,
        kalshi_by_ticker=kalshi_by_ticker,
        valid_news_ids=valid_news_ids,
    )

    # 6. Log for the audit trail
    _log_call(
        {
            "agent": "scenario",
            "seed_event": seed_event,
            "model": model,
            "n_kalshi_markets": len(markets),
            "n_news_items": len(news),
            "n_raw_scenarios": len(raw_scenarios),
            "n_final_scenarios": len(scenarios),
            "usage": getattr(response, "usage", None) and {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_read_input_tokens": getattr(
                    response.usage, "cache_read_input_tokens", None
                ),
                "cache_creation_input_tokens": getattr(
                    response.usage, "cache_creation_input_tokens", None
                ),
            },
        }
    )

    return scenarios
