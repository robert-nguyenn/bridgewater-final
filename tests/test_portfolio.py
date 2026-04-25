from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.agents import portfolio
from src.agents.portfolio import (
    PortfolioImpact,
    _bucket_terminals,
    _edge_stats,
    _normalize_class,
    _post_process,
)
from src.types import CausalGraph, Edge, Node, ToolBundle


def _node(id: str, asset_class: str | None = None, label: str = "x", layer: int = 2) -> Node:
    return Node(id=id, label=label, description="desc", layer=layer, asset_class=asset_class)


def test_portfolio_module_has_run():
    assert hasattr(portfolio, "run")


def test_portfolio_impact_constructs_with_defaults():
    p = PortfolioImpact(asset_class="equities", direction="up", summary="s")
    assert p.confidence == 0.0
    assert p.tickers == []
    assert p.magnitude_label == "unclear"


def test_normalize_class_maps_known_classes_lowercase():
    assert _normalize_class("Equities") == "equities"
    assert _normalize_class("FX") == "fx"


def test_normalize_class_maps_unknown_to_unclassified():
    assert _normalize_class(None) == "unclassified"
    assert _normalize_class("") == "unclassified"
    assert _normalize_class("crypto") == "unclassified"


def test_bucket_terminals_groups_by_normalized_class():
    nodes = [
        _node("a", "equities"),
        _node("b", "Equities"),
        _node("c", "fx"),
        _node("d", None),
    ]
    buckets = _bucket_terminals(nodes)
    assert {n.id for n in buckets["equities"]} == {"a", "b"}
    assert {n.id for n in buckets["fx"]} == {"c"}
    assert {n.id for n in buckets["unclassified"]} == {"d"}


def test_edge_stats_aggregates_inbound_per_node():
    g = CausalGraph(
        nodes={"a": _node("a"), "b": _node("b")},
        edges=[
            Edge(src="a", dst="b", mechanism="m", sensitivity=0.4, confidence=0.6),
            Edge(src="a", dst="b", mechanism="m2", sensitivity=0.6, confidence=0.8),
        ],
    )
    stats = _edge_stats(g)
    assert stats["b"]["n_inbound"] == 2
    assert stats["b"]["avg_confidence"] == 0.7
    assert stats["b"]["avg_sensitivity"] == 0.5


def test_edge_stats_handles_no_graph():
    assert _edge_stats(None) == {}
    assert _edge_stats(CausalGraph()) == {}


def test_post_process_drops_unknown_asset_class():
    raw = [
        {
            "asset_class": "crypto",  # not in input, drop
            "direction": "up",
            "summary": "x",
            "magnitude_label": "small",
            "confidence": 0.5,
            "time_horizon_days": 30,
            "contributing_nodes": ["n1"],
        }
    ]
    out = _post_process(raw, valid_node_ids={"n1"}, valid_classes_in_input={"equities"})
    assert out == []


def test_post_process_drops_class_not_in_input():
    raw = [
        {
            "asset_class": "rates",  # we did not pass any rates terminals
            "direction": "up",
            "summary": "x",
            "magnitude_label": "small",
            "confidence": 0.5,
            "time_horizon_days": 30,
            "contributing_nodes": ["n1"],
        }
    ]
    out = _post_process(raw, valid_node_ids={"n1"}, valid_classes_in_input={"equities"})
    assert out == []


def test_post_process_collapses_duplicate_classes_first_wins():
    raw = [
        {
            "asset_class": "equities",
            "direction": "up",
            "summary": "first",
            "magnitude_label": "small",
            "confidence": 0.5,
            "time_horizon_days": 30,
            "contributing_nodes": ["n1"],
        },
        {
            "asset_class": "equities",
            "direction": "down",
            "summary": "second",
            "magnitude_label": "large",
            "confidence": 0.9,
            "time_horizon_days": 60,
            "contributing_nodes": ["n1"],
        },
    ]
    out = _post_process(raw, valid_node_ids={"n1"}, valid_classes_in_input={"equities"})
    assert len(out) == 1
    assert out[0].summary == "first"


def test_post_process_strips_hallucinated_node_ids_from_drivers():
    raw = [
        {
            "asset_class": "equities",
            "direction": "up",
            "summary": "x",
            "magnitude_label": "moderate",
            "confidence": 0.6,
            "time_horizon_days": 30,
            "contributing_nodes": ["real-1", "real-2", "BOGUS"],
            "key_drivers": ["real-1", "BOGUS"],
            "offsets": ["real-2", "ALSO-BOGUS"],
        }
    ]
    out = _post_process(raw, valid_node_ids={"real-1", "real-2"}, valid_classes_in_input={"equities"})
    assert out[0].contributing_nodes == ["real-1", "real-2"]
    assert out[0].key_drivers == ["real-1"]
    assert out[0].offsets == ["real-2"]


def test_post_process_clamps_confidence_and_horizon_and_normalizes_tickers():
    raw = [
        {
            "asset_class": "equities",
            "direction": "weird-value",  # invalid enum
            "summary": "x",
            "magnitude_label": "huge",  # invalid enum
            "confidence": 1.7,  # over the cap
            "time_horizon_days": 9999,  # over cap
            "tickers": [" nvda ", "soxx", "", "asml", "tsm", "amat", "OVERFLOW"],
            "contributing_nodes": ["n1"],
        }
    ]
    out = _post_process(raw, valid_node_ids={"n1"}, valid_classes_in_input={"equities"})
    assert out[0].direction == "mixed"
    assert out[0].magnitude_label == "unclear"
    assert out[0].confidence == 1.0
    assert out[0].time_horizon_days == 365
    # uppercased, stripped, empties dropped, capped at 6
    assert out[0].tickers == ["NVDA", "SOXX", "ASML", "TSM", "AMAT", "OVERFLOW"]


def test_run_returns_empty_for_empty_terminals():
    out = portfolio.run([], tools=ToolBundle(), model="claude-opus-4-7")
    assert out == []


def test_run_returns_empty_when_only_unclassified():
    nodes = [_node("a", None), _node("b", None)]
    out = portfolio.run(nodes, tools=ToolBundle(), model="claude-opus-4-7")
    assert out == []


def test_run_with_mocked_client_returns_parsed_impacts():
    nodes = [
        _node("eq-1", "equities", label="Chip ASP rises"),
        _node("eq-2", "equities", label="OEM margin compression"),
        _node("fx-1", "fx", label="USD strengthens"),
    ]
    g = CausalGraph(
        nodes={n.id: n for n in nodes},
        edges=[
            Edge(src="root", dst="eq-1", mechanism="m", sensitivity=0.7, confidence=0.8),
            Edge(src="root", dst="fx-1", mechanism="m", sensitivity=0.5, confidence=0.6),
        ],
    )

    fake_tool_use = SimpleNamespace(
        type="tool_use",
        name="submit_portfolio_impacts",
        input={
            "impacts": [
                {
                    "asset_class": "equities",
                    "direction": "mixed",
                    "summary": "Chip ASP rises but OEM margins compress.",
                    "tickers": ["NVDA", "SOXX"],
                    "magnitude_label": "moderate",
                    "confidence": 0.65,
                    "key_drivers": ["eq-1", "BOGUS"],
                    "offsets": ["eq-2"],
                    "time_horizon_days": 90,
                    "contributing_nodes": ["eq-1", "eq-2"],
                },
                {
                    "asset_class": "fx",
                    "direction": "up",
                    "summary": "USD strengthens via rate differential.",
                    "tickers": ["DX=F"],
                    "magnitude_label": "moderate",
                    "confidence": 0.7,
                    "key_drivers": ["fx-1"],
                    "offsets": [],
                    "time_horizon_days": 60,
                    "contributing_nodes": ["fx-1"],
                },
            ]
        },
    )
    fake_response = SimpleNamespace(
        content=[fake_tool_use],
        usage=SimpleNamespace(
            input_tokens=120, output_tokens=200,
            cache_read_input_tokens=0, cache_creation_input_tokens=900,
        ),
    )
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    out = portfolio.run(
        nodes,
        tools=ToolBundle(),
        model="claude-opus-4-7",
        graph=g,
        seed_event="25% tariff on Chinese semiconductors",
        client=fake_client,
    )

    assert len(out) == 2
    eq = next(i for i in out if i.asset_class == "equities")
    assert eq.direction == "mixed"
    assert eq.tickers == ["NVDA", "SOXX"]
    assert eq.key_drivers == ["eq-1"]  # BOGUS dropped
    assert eq.offsets == ["eq-2"]
    fake_client.messages.create.assert_called_once()
