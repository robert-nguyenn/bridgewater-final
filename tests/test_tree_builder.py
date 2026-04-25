from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from src.agents import sensitivity, tree_builder
from src.types import (
    CaseStudy,
    CausalGraph,
    Edge,
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
        return ToolError(tool="fred", args={"series_id": series_id}, message="missing")


def make_case_study() -> CaseStudy:
    return CaseStudy(
        name="2018 Section 301 tariffs",
        date_range=(date(2018, 7, 6), date(2019, 6, 30)),
        triggering_event="US imposes Section 301 tariffs on Chinese imports",
        macro_snapshot=MacroSnapshot(cpi_yoy=2.9, fed_funds=2.0),
        similarity_score=0.7,
        subtree=CausalGraph(),
    )


def series_with_spike(magnitude: float = 20.0) -> pd.DataFrame:
    idx = pd.date_range("2018-06-01", periods=80, freq="W")
    values = [100.0] * 30 + [100.0 + magnitude] * 50
    return pd.DataFrame({"value": values}, index=idx)


def smart_stub(
    *,
    propose_count: int = 3,
    score_payload: dict | None = None,
    challenge_action: str = "keep",
    asset_class_cycle: list[str | None] | None = None,
):
    """Routes by sentinel in the prompt. Same callable serves both modules."""
    score_payload = score_payload or {
        "sensitivity": 0.6,
        "confidence": 0.55,
        "mechanism_refined": "stub mechanism",
        "supporting_data": [
            {"series_id": "DTWEXBGS", "peak_z": 2.0, "interpretation": "stub"}
        ],
        "magnitude_estimate": 0.05,
        "keep": True,
        "keep_reason": "stub keep",
    }
    asset_class_cycle = asset_class_cycle or [
        "equities",
        "rates",
        "fx",
        "macro",
        "commodities",
    ]

    def stub(prompt: str, *, model: str, system: str = "") -> str:
        if "PROPOSE_CHILDREN" in prompt:
            return json.dumps(
                [
                    {
                        "label": f"effect_{i}",
                        "description": f"description {i}",
                        "asset_class": asset_class_cycle[i % len(asset_class_cycle)],
                        "mechanism": f"parent drives effect_{i}",
                    }
                    for i in range(propose_count)
                ]
            )
        if "CHALLENGE_CANDIDATE" in prompt:
            return json.dumps(
                {"action": challenge_action, "merge_with": None, "reason": "stub"}
            )
        if "STEP 1" in prompt:
            return json.dumps(
                {"fred_series": ["DTWEXBGS"], "tickers": [], "reasoning": "."}
            )
        if "STEP 2" in prompt:
            return json.dumps(score_payload)
        return "{}"

    return stub


def test_tree_builder_module_has_run_and_build_subtree():
    assert hasattr(tree_builder, "run")
    assert hasattr(tree_builder, "build_subtree")


def test_tree_builder_rejects_cycles():
    g = CausalGraph()
    g.nodes["a"] = Node(id="a", label="A", description="", layer=0)
    g.nodes["b"] = Node(id="b", label="B", description="", layer=1)
    g.root = "a"
    g.edges.append(
        Edge(src="a", dst="b", mechanism="", sensitivity=0.5, confidence=0.5)
    )

    cyclic = Edge(src="b", dst="a", mechanism="", sensitivity=0.5, confidence=0.5)
    assert tree_builder._add_edge_if_dag(g, cyclic) is False
    assert len(g.edges) == 1


def test_tree_builder_respects_max_nodes(monkeypatch):
    stub = smart_stub(propose_count=10)
    monkeypatch.setattr(tree_builder, "_call_model", stub)
    monkeypatch.setattr(sensitivity, "_call_model", stub)
    fred = StubFRED({"DTWEXBGS": series_with_spike()})
    tools = ToolBundle(fred=fred)

    result = tree_builder.build_subtree(
        make_case_study(),
        tools=tools,
        max_layers=3,
        max_nodes=20,
    )
    assert len(result.subtree.nodes) <= 20
    assert len(result.subtree.nodes) > 1


def test_tree_builder_assigns_asset_class_to_leaves(monkeypatch):
    """Layer-1 candidates with asset_class=None must be dropped from final leaves."""

    def stub(prompt: str, *, model: str, system: str = "") -> str:
        if "PROPOSE_CHILDREN" in prompt:
            return json.dumps(
                [
                    {
                        "label": "good",
                        "description": "valid",
                        "asset_class": "equities",
                        "mechanism": "m",
                    },
                    {
                        "label": "bad",
                        "description": "missing class",
                        "asset_class": None,
                        "mechanism": "m",
                    },
                ]
            )
        if "CHALLENGE_CANDIDATE" in prompt:
            return json.dumps({"action": "keep", "merge_with": None, "reason": "ok"})
        if "STEP 1" in prompt:
            return json.dumps(
                {"fred_series": ["DTWEXBGS"], "tickers": [], "reasoning": "."}
            )
        if "STEP 2" in prompt:
            return json.dumps(
                {
                    "sensitivity": 0.6,
                    "confidence": 0.6,
                    "mechanism_refined": "m",
                    "supporting_data": [
                        {"series_id": "DTWEXBGS", "peak_z": 2.0, "interpretation": "x"}
                    ],
                    "magnitude_estimate": None,
                    "keep": True,
                    "keep_reason": "ok",
                }
            )
        return "{}"

    monkeypatch.setattr(tree_builder, "_call_model", stub)
    monkeypatch.setattr(sensitivity, "_call_model", stub)
    fred = StubFRED({"DTWEXBGS": series_with_spike()})
    tools = ToolBundle(fred=fred)

    result = tree_builder.build_subtree(
        make_case_study(),
        tools=tools,
        max_layers=1,
        max_nodes=10,
    )
    leaves = tree_builder._leaves(result.subtree)
    assert leaves, "expected at least one leaf in the subtree"
    for leaf_id in leaves:
        assert result.subtree.nodes[leaf_id].asset_class is not None


def test_tree_builder_drop_after_score_excludes_node(monkeypatch):
    """Candidates the scorer drops must not enter the graph."""

    def stub(prompt: str, *, model: str, system: str = "") -> str:
        if "PROPOSE_CHILDREN" in prompt:
            return json.dumps(
                [
                    {
                        "label": "weak",
                        "description": "weak edge",
                        "asset_class": "equities",
                        "mechanism": "m",
                    }
                ]
            )
        if "CHALLENGE_CANDIDATE" in prompt:
            return json.dumps({"action": "keep", "merge_with": None, "reason": "ok"})
        if "STEP 1" in prompt:
            return json.dumps(
                {"fred_series": ["DTWEXBGS"], "tickers": [], "reasoning": "."}
            )
        if "STEP 2" in prompt:
            return json.dumps(
                {
                    "sensitivity": 0.05,
                    "confidence": 0.1,
                    "mechanism_refined": "m",
                    "supporting_data": [],
                    "magnitude_estimate": None,
                    "keep": False,
                    "keep_reason": "below rubric",
                }
            )
        return "{}"

    monkeypatch.setattr(tree_builder, "_call_model", stub)
    monkeypatch.setattr(sensitivity, "_call_model", stub)
    fred = StubFRED({"DTWEXBGS": series_with_spike()})
    tools = ToolBundle(fred=fred)

    result = tree_builder.build_subtree(
        make_case_study(), tools=tools, max_layers=1, max_nodes=10
    )
    # Only the root remains.
    assert len(result.subtree.nodes) == 1
    assert result.subtree.root in result.subtree.nodes
    assert result.subtree.edges == []


def test_tree_builder_end_to_end_on_2018_tariffs(monkeypatch):
    stub = smart_stub(propose_count=3)
    monkeypatch.setattr(tree_builder, "_call_model", stub)
    monkeypatch.setattr(sensitivity, "_call_model", stub)
    fred = StubFRED({"DTWEXBGS": series_with_spike(20.0)})
    tools = ToolBundle(fred=fred)

    result = tree_builder.build_subtree(
        make_case_study(),
        tools=tools,
        max_layers=2,
        max_nodes=40,
    )

    layers_present = {n.layer for n in result.subtree.nodes.values()}
    assert 0 in layers_present
    assert 1 in layers_present
    assert max(layers_present) >= 1

    high_conf_edges = [e for e in result.subtree.edges if e.confidence >= 0.5]
    assert len(high_conf_edges) >= 1

    layer_ge_1 = [n for n in result.subtree.nodes.values() if n.layer >= 1]
    assert all(n.asset_class is not None for n in layer_ge_1)
    asset_classes = {n.asset_class for n in layer_ge_1}
    assert {"equities", "rates", "fx", "macro", "commodities"} & asset_classes
