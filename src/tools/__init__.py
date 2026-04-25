"""Tool layer. `make_default_tools()` returns a ToolBundle wired to the live
FRED, Yahoo, and HF wrappers. Tests pass their own stubs into ToolBundle
directly and never call this factory."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Optional

from src.types import ToolBundle, ToolError

logger = logging.getLogger(__name__)


def _parse_citation(s: str) -> tuple[str, str]:
    """'FRED:CPIAUCSL' → ('fred', 'CPIAUCSL'). 'NVDA' (no prefix) → ('unknown', 'NVDA')."""
    if not isinstance(s, str):
        return "unknown", str(s)
    if ":" in s:
        kind, ref = s.split(":", 1)
        return kind.strip().lower(), ref.strip()
    return "unknown", s.strip()


def _validate_one(citation: str, tools: ToolBundle) -> str:
    """Validate one citation against the live tools.

    Returns one of:
    - "ok" — citation refers to a real, fetchable series/ticker.
    - "missing" — citation parsed as a FRED/ticker reference but the tool
      returned ToolError (series doesn't exist, no data, or rate-limited).
    - "unverifiable" — citation is a category we can't check (e.g.
      "episode:2018 tariffs", "speech:..."), or no matching tool wired.
    """
    kind, ref = _parse_citation(citation)
    if not ref:
        return "unverifiable"
    today = date.today()
    recent_start = today - timedelta(days=90)

    if kind in ("fred", "fred_series", "series"):
        if tools is None or tools.fred is None or not hasattr(tools.fred, "fred_get_series"):
            return "unverifiable"
        try:
            result = tools.fred.fred_get_series(ref, recent_start, today)
        except Exception as exc:
            logger.warning("fred validation raised for %s: %s", ref, exc)
            return "missing"
        return "missing" if isinstance(result, ToolError) else "ok"

    if kind in ("ticker", "yahoo", "stock"):
        if tools is None or tools.yahoo is None or not hasattr(tools.yahoo, "yahoo_prices"):
            return "unverifiable"
        try:
            result = tools.yahoo.yahoo_prices(ref, recent_start, today)
        except Exception as exc:
            logger.warning("yahoo validation raised for %s: %s", ref, exc)
            return "missing"
        return "missing" if isinstance(result, ToolError) else "ok"

    # Episodes, speeches, articles, generic refs — we don't have a
    # source-of-truth list to check against.
    return "unverifiable"


def validate_citations(
    citations: list[str],
    tools: ToolBundle,
    *,
    max_workers: int = 4,
) -> dict[str, str]:
    """Validate a list of citation strings against live FRED/Yahoo data.

    Deduplicates input, runs checks in parallel (each backed by the disk
    cache so repeated runs are fast), and returns ``{citation: status}``.
    Statuses: "ok" / "missing" / "unverifiable".
    """
    if not citations:
        return {}
    unique = list({c for c in citations if isinstance(c, str) and c.strip()})
    if not unique:
        return {}
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_cit = {
            executor.submit(_validate_one, c, tools): c for c in unique
        }
        for fut in as_completed(future_to_cit):
            cit = future_to_cit[fut]
            try:
                results[cit] = fut.result()
            except Exception as exc:
                logger.warning("citation validation crashed for %s: %s", cit, exc)
                results[cit] = "missing"
    return results


def make_default_tools() -> ToolBundle:
    """Build a ToolBundle pointing at the live tool modules.

    Each `tools.<source>` slot is the module itself, so callers reach functions
    via `tools.fred.fred_get_series(...)`. Modules satisfy the duck-typed shape
    that the agents expect (same shape as the test stubs)."""
    from src.tools import fred, hf, yahoo

    return ToolBundle(
        fred=fred,
        yahoo=yahoo,
        hf=hf,
        central_bank=hf,  # central_bank_search lives in hf.py per CLAUDE.md
        news=hf,
    )


__all__ = ["make_default_tools", "validate_citations"]
