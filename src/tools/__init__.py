"""Tool layer. `make_default_tools()` returns a ToolBundle wired to the live
FRED, Yahoo, and HF wrappers. Tests pass their own stubs into ToolBundle
directly and never call this factory."""

from __future__ import annotations

from src.types import ToolBundle


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


__all__ = ["make_default_tools"]
