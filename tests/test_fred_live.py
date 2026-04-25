"""Live FRED smoke tests. Skipped if FRED_API_KEY is not set.

Run only these:
    pytest tests/test_fred_live.py -v -s

The point of this file is to verify, end to end, that:
    1. The .env wired through to src/config.FRED_API_KEY.
    2. fredapi reaches the FRED API and returns real data.
    3. The disk cache populates so a second call is instant.
    4. fred_get_series returns the DataFrame shape downstream agents expect.
    5. macro_snapshot returns a populated MacroSnapshot for a real date.
"""

from __future__ import annotations

import time
from datetime import date

import pandas as pd
import pytest

from src.config import FRED_API_KEY
from src.tools import fred as fred_tool
from src.types import MacroSnapshot, ToolError

pytestmark = pytest.mark.skipif(
    not FRED_API_KEY,
    reason="FRED_API_KEY not set in .env; live tests skipped.",
)


def test_fred_get_series_round_trip():
    df = fred_tool.fred_get_series("DGS10", date(2018, 1, 1), date(2018, 6, 30))
    assert not isinstance(df, ToolError), df
    assert isinstance(df, pd.DataFrame)
    assert "value" in df.columns
    assert len(df) > 50  # ~125 trading days expected
    assert df["value"].dtype.kind in "fi"
    assert df.index.is_monotonic_increasing


def test_fred_disk_cache_hot_path():
    # Pre-warm.
    fred_tool.fred_get_series("DGS10", date(2018, 1, 1), date(2018, 6, 30))
    t0 = time.time()
    df = fred_tool.fred_get_series("DGS10", date(2018, 1, 1), date(2018, 6, 30))
    elapsed = time.time() - t0
    assert not isinstance(df, ToolError)
    assert elapsed < 0.25, f"cache miss? second call took {elapsed:.3f}s"


def test_fred_unknown_series_returns_tool_error():
    result = fred_tool.fred_get_series("NOT_A_REAL_SERIES_xyz123", date(2018, 1, 1), date(2018, 6, 30))
    assert isinstance(result, ToolError)
    assert "fred" == result.tool


def test_macro_snapshot_populated():
    snap = fred_tool.macro_snapshot(date(2024, 6, 30))
    assert not isinstance(snap, ToolError), snap
    assert isinstance(snap, MacroSnapshot)
    # At least the daily series should be present for a recent date.
    assert snap.fed_funds is not None
    assert snap.ten_year is not None


def test_fred_find_extrema_returns_episodes():
    eps = fred_tool.fred_find_extrema(
        "DGS10", threshold_zscore=2.5, window=60,
        history_start=date(2000, 1, 1), history_end=date(2024, 1, 1),
    )
    assert not isinstance(eps, ToolError), eps
    assert isinstance(eps, list)
    # 25 years of daily data with z>=2.5 will surface several episodes.
    assert len(eps) >= 1
    for ep in eps:
        assert ep.series_id == "DGS10"
        assert ep.start <= ep.end


def test_sensitivity_against_real_fred():
    """End to end: SensitivityAgent.score_edge with the live FRED tool, model
    call monkeypatched. Proves the tool wiring works through to the agent."""
    import json

    from src.agents import sensitivity
    from src.tools import make_default_tools
    from src.types import CaseStudy, CausalGraph, MacroSnapshot, Node

    refs = json.dumps({"fred_series": ["DGS10"], "tickers": [], "reasoning": "live test"})
    score = json.dumps({
        "sensitivity": 0.6, "confidence": 0.55,
        "mechanism_refined": "live test",
        "supporting_data": [{"series_id": "DGS10", "peak_z": 2.0, "interpretation": "real"}],
        "magnitude_estimate": -25.0, "keep": True, "keep_reason": "ok",
    })
    queue = [refs, score]
    sensitivity_call = sensitivity._call_model

    def stub(prompt, *, model, system=""):
        return queue.pop(0)

    sensitivity._call_model = stub
    try:
        case = CaseStudy(
            name="Q4 2018 risk-off",
            date_range=(date(2018, 10, 1), date(2018, 12, 31)),
            triggering_event="test",
            macro_snapshot=MacroSnapshot(),
            similarity_score=0.5,
            subtree=CausalGraph(),
        )
        tools = make_default_tools()
        result = sensitivity.score_edge(
            parent=Node(id="p", label="risk-off", description="...", layer=0),
            candidate=Node(id="c", label="10y rallies", description="...", layer=1),
            mechanism="risk-off bids duration",
            case_study=case,
            tools=tools,
            model="m",
        )
    finally:
        sensitivity._call_model = sensitivity_call

    assert result.keep
    # supporting_data should now contain a real, populated Evidence with a
    # peak_z computed from the actual FRED series.
    real = [ev for ev in result.supporting_data if ev.payload]
    assert real, "no real FRED-backed evidence attached"
    payload = real[0].payload
    assert "peak_z" in payload
    assert isinstance(payload["peak_z"], (int, float))
