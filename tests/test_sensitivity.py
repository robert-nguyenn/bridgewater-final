from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from src.agents import sensitivity
from src.types import (
    CaseStudy,
    CausalGraph,
    MacroSnapshot,
    Node,
    ToolBundle,
    ToolError,
)


class StubFRED:
    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = frames

    def fred_get_series(self, series_id: str, start, end):
        if series_id in self.frames:
            return self.frames[series_id].copy()
        return ToolError(tool="fred", args={"series_id": series_id}, message="not found")


def make_case_study() -> CaseStudy:
    return CaseStudy(
        name="2018 Section 301 tariffs",
        date_range=(date(2018, 7, 6), date(2019, 6, 30)),
        triggering_event="US imposes Section 301 tariffs on Chinese imports",
        macro_snapshot=MacroSnapshot(cpi_yoy=2.9, fed_funds=2.0),
        similarity_score=0.7,
        subtree=CausalGraph(),
    )


def make_series(values: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2018-06-01", periods=len(values), freq="W")
    return pd.DataFrame({"value": values}, index=idx)


def queue_stub(responses: list[str]):
    queue = list(responses)

    def stub(prompt: str, *, model: str, system: str = "") -> str:
        return queue.pop(0)

    return stub


def test_sensitivity_module_has_run_and_score_edge():
    assert hasattr(sensitivity, "run")
    assert hasattr(sensitivity, "score_edge")


def test_sensitivity_drops_low_confidence_low_sensitivity(monkeypatch):
    refs = json.dumps({"fred_series": ["DTWEXBGS"], "tickers": [], "reasoning": "."})
    score = json.dumps(
        {
            "sensitivity": 0.1,
            "confidence": 0.2,
            "mechanism_refined": "weak",
            "supporting_data": [],
            "magnitude_estimate": None,
            "keep": False,
            "keep_reason": "no signal",
        }
    )
    monkeypatch.setattr(sensitivity, "_call_model", queue_stub([refs, score]))

    parent = Node(id="p", label="USD up", description="USD strengthens", layer=0)
    cand = Node(id="c", label="X", description="some downstream variable", layer=1)
    fred = StubFRED({"DTWEXBGS": make_series([100.0] * 80)})
    tools = ToolBundle(fred=fred)

    result = sensitivity.score_edge(
        parent, cand, "USD up drives X", make_case_study(), tools=tools, model="m"
    )
    assert result.keep is False
    assert result.sensitivity == pytest.approx(0.1)
    assert result.confidence == pytest.approx(0.2)


def test_sensitivity_keeps_high_sensitivity_low_confidence(monkeypatch):
    refs = json.dumps({"fred_series": ["DTWEXBGS"], "tickers": [], "reasoning": "."})
    score = json.dumps(
        {
            "sensitivity": 0.7,
            "confidence": 0.25,
            "mechanism_refined": "strong directional but thin evidence",
            "supporting_data": [
                {"series_id": "DTWEXBGS", "peak_z": 2.3, "interpretation": "spike"}
            ],
            "magnitude_estimate": -0.05,
            "keep": True,
            "keep_reason": "directional move worth retaining for adversary stage",
        }
    )
    monkeypatch.setattr(sensitivity, "_call_model", queue_stub([refs, score]))

    parent = Node(id="p", label="USD up", description="...", layer=0)
    cand = Node(id="c", label="commodity prices", description="...", layer=1)
    # Series with a real shift so summarize_series produces non-zero peak_z.
    values = [100.0] * 30 + [120.0] * 50
    fred = StubFRED({"DTWEXBGS": make_series(values)})
    tools = ToolBundle(fred=fred)

    result = sensitivity.score_edge(
        parent, cand, "USD up squeezes commodities", make_case_study(), tools=tools, model="m"
    )
    assert result.keep is True
    assert result.sensitivity == pytest.approx(0.7)
    assert result.confidence == pytest.approx(0.25)
    assert any(ev.kind == "fred_series" and ev.payload for ev in result.supporting_data)


def test_sensitivity_caps_confidence_when_no_data(monkeypatch):
    refs = json.dumps({"fred_series": [], "tickers": [], "reasoning": "."})
    score = json.dumps(
        {
            "sensitivity": 0.5,
            "confidence": 0.85,
            "mechanism_refined": "asserted",
            "supporting_data": [],
            "magnitude_estimate": None,
            "keep": True,
            "keep_reason": "model asserts confidence without data",
        }
    )
    monkeypatch.setattr(sensitivity, "_call_model", queue_stub([refs, score]))

    parent = Node(id="p", label="x", description="x", layer=0)
    cand = Node(id="c", label="y", description="y", layer=1)
    tools = ToolBundle(fred=StubFRED({}))

    result = sensitivity.score_edge(
        parent, cand, "...", make_case_study(), tools=tools, model="m"
    )
    assert result.confidence <= sensitivity.PRIORS_ONLY_CAP


def test_sensitivity_records_tool_errors(monkeypatch):
    refs = json.dumps({"fred_series": ["MISSING"], "tickers": [], "reasoning": "."})
    score = json.dumps(
        {
            "sensitivity": 0.4,
            "confidence": 0.4,
            "mechanism_refined": "...",
            "supporting_data": [],
            "magnitude_estimate": None,
            "keep": True,
            "keep_reason": "...",
        }
    )
    monkeypatch.setattr(sensitivity, "_call_model", queue_stub([refs, score]))

    parent = Node(id="p", label="x", description="x", layer=0)
    cand = Node(id="c", label="y", description="y", layer=1)
    tools = ToolBundle(fred=StubFRED({}))

    result = sensitivity.score_edge(
        parent, cand, "...", make_case_study(), tools=tools, model="m"
    )
    # Confidence capped because no usable data even though the series was named.
    assert result.confidence <= sensitivity.PRIORS_ONLY_CAP
    assert any(
        ev.note and "tool_error" in ev.note for ev in result.supporting_data
    )
