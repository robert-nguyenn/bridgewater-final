from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.agents import idea
from src.agents.idea import (
    _coerce_evidence,
    _normalize_class,
    _post_process,
)
from src.types import Evidence, ToolBundle


def test_idea_module_has_run():
    assert hasattr(idea, "run")


def test_normalize_class_lowercases_known_classes():
    assert _normalize_class("Equities") == "equities"
    assert _normalize_class("FX") == "fx"
    assert _normalize_class("MACRO") == "macro"


def test_normalize_class_returns_none_for_unknown_or_empty():
    assert _normalize_class(None) is None
    assert _normalize_class("") is None
    assert _normalize_class("crypto") is None


def test_coerce_evidence_drops_invalid_kinds_and_empty_refs():
    raw = [
        {"kind": "fred_series", "ref": "CPIAUCSL", "note": "headline CPI"},
        {"kind": "ticker", "ref": "NVDA"},
        {"kind": "made_up", "ref": "X"},  # invalid kind
        {"kind": "ticker", "ref": ""},  # empty ref
        "garbage string",  # not a dict
    ]
    out = _coerce_evidence(raw)
    assert len(out) == 2
    assert out[0] == Evidence(kind="fred_series", ref="CPIAUCSL", note="headline CPI")
    assert out[1] == Evidence(kind="ticker", ref="NVDA", note=None)


def test_coerce_evidence_handles_none_and_empty():
    assert _coerce_evidence(None) == []
    assert _coerce_evidence([]) == []


def test_post_process_assigns_sequential_ids_and_layer_one():
    raw = [
        {"label": "USD strengthens", "description": "x"},
        {"label": "Chip ASP rises", "description": "y"},
    ]
    out = _post_process(raw)
    assert [n.id for n in out] == ["n1", "n2"]
    assert all(n.layer == 1 for n in out)


def test_post_process_dedups_labels_case_insensitive():
    raw = [
        {"label": "USD strengthens", "description": "x"},
        {"label": "usd strengthens", "description": "dup"},
        {"label": "Chip ASP rises", "description": "y"},
    ]
    out = _post_process(raw)
    assert len(out) == 2
    assert {n.label for n in out} == {"USD strengthens", "Chip ASP rises"}


def test_post_process_drops_empty_label_or_description():
    raw = [
        {"label": "", "description": "no label"},
        {"label": "no description", "description": ""},
        {"label": "good", "description": "good"},
    ]
    out = _post_process(raw)
    assert len(out) == 1
    assert out[0].label == "good"


def test_post_process_caps_at_max_nodes():
    raw = [{"label": f"label {i}", "description": "d"} for i in range(20)]
    out = _post_process(raw)
    assert len(out) == 8


def test_post_process_coerces_magnitude_to_float_or_none():
    raw = [
        {"label": "a", "description": "d", "magnitude_estimate": "0.12"},
        {"label": "b", "description": "d", "magnitude_estimate": "garbage"},
        {"label": "c", "description": "d", "magnitude_estimate": None},
    ]
    out = _post_process(raw)
    assert out[0].magnitude_estimate == 0.12
    assert out[1].magnitude_estimate is None
    assert out[2].magnitude_estimate is None


def test_run_returns_empty_for_empty_event():
    assert idea.run("", tools=ToolBundle(), model="claude-opus-4-7") == []
    assert idea.run("   ", tools=ToolBundle(), model="claude-opus-4-7") == []


def test_run_with_mocked_client_returns_parsed_nodes():
    fake_tool_use = SimpleNamespace(
        type="tool_use",
        name="submit_first_order_nodes",
        input={
            "nodes": [
                {
                    "label": "Chip ASP +12% in US",
                    "description": "Tariff pass-through to US OEMs.",
                    "asset_class": "equities",
                    "magnitude_estimate": 0.12,
                    "evidence": [{"kind": "ticker", "ref": "NVDA", "note": "designer pricing power"}],
                },
                {
                    "label": "USD strengthens vs CNY",
                    "description": "Capital outflow.",
                    "asset_class": "fx",
                    "magnitude_estimate": 0.04,
                    "evidence": [{"kind": "fred_series", "ref": "DEXCHUS"}],
                },
                {
                    "label": "Fed wait-and-see on inflation impulse",
                    "description": "Fed gauges tariff pass-through duration before reacting.",
                    "asset_class": "rates",
                    "magnitude_estimate": None,
                    "evidence": [],
                },
            ]
        },
    )
    fake_response = SimpleNamespace(
        content=[fake_tool_use],
        usage=SimpleNamespace(
            input_tokens=80, output_tokens=200,
            cache_read_input_tokens=0, cache_creation_input_tokens=600,
        ),
    )
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    out = idea.run(
        "25% tariff on Chinese semiconductors",
        tools=ToolBundle(),
        model="claude-opus-4-7",
        client=fake_client,
    )

    assert len(out) == 3
    assert [n.id for n in out] == ["n1", "n2", "n3"]
    assert all(n.layer == 1 for n in out)
    assert out[0].asset_class == "equities"
    assert out[0].evidence[0].ref == "NVDA"
    assert out[1].asset_class == "fx"
    fake_client.messages.create.assert_called_once()
