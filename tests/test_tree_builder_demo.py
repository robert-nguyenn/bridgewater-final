"""End-to-end demonstration of the TreeBuilder + Sensitivity pipeline.

Runs the full Stage 3 (Case Study Tree Construction) loop against in-memory
stubs for both the model and the FRED/Yahoo tools, so it executes in under a
second with no external services.

Run modes:
    pytest tests/test_tree_builder_demo.py -s   # pytest with live trace
    python -m tests.test_tree_builder_demo      # standalone script

What it demonstrates, end to end:
    1. TreeBuilder._propose_children: the model proposes 3 to 5 child nodes
       per parent, each with a candidate mechanism and asset class.
    2. SensitivityAgent.score_edge -> _propose_data_refs (STEP 1):
       the model picks which FRED series and tickers should move.
    3. SensitivityAgent.score_edge -> tool calls + _summarize_series:
       the FRED stub returns DataFrames; we compute pre/post means, peak_z,
       time-to-peak. The agent reasons over the stats, not the raw series.
    4. SensitivityAgent.score_edge -> scoring call (STEP 2):
       the model returns sensitivity, confidence, magnitude, keep flag.
       The rubric drops candidates with confidence < 0.3 AND sensitivity < 0.2.
    5. TreeBuilder._challenge_candidate: a lightweight adversary asks if the
       node is a restatement, redundant with a sibling, or wrong asset class.
       This demo includes drop, merge, and keep outcomes.
    6. Cycle rejection: networkx.is_directed_acyclic_graph after every insert.
    7. Layer stop conditions: max_layers, max_nodes, layer-confidence floor.
    8. Asset-class enforcement: leaves with asset_class=None are dropped.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from typing import Any

import pandas as pd

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


# ---------------------------------------------------------------------------
# STUB TOOLS (stand in for src/tools/fred.py and src/tools/yahoo.py)
# ---------------------------------------------------------------------------


class DemoFRED:
    """In-memory FRED stub. Returns realistic shapes for the 2018 tariff window."""

    def __init__(self) -> None:
        idx = pd.date_range("2018-06-01", periods=80, freq="W")
        self.frames: dict[str, pd.DataFrame] = {
            "DTWEXBGS": pd.DataFrame(
                {"value": [120.0] * 30 + [126.0 + i * 0.04 for i in range(50)]},
                index=idx,
            ),
            "DGS10": pd.DataFrame(
                {"value": [2.85] * 30 + [2.85 - i * 0.012 for i in range(50)]},
                index=idx,
            ),
            "BAMLH0A0HYM2": pd.DataFrame(
                {"value": [3.40] * 30 + [3.40 + i * 0.022 for i in range(50)]},
                index=idx,
            ),
            "DCOILWTICO": pd.DataFrame(
                {"value": [70.0 + (i % 5) * 0.3 for i in range(80)]},
                index=idx,
            ),
            "CPIAUCSL": pd.DataFrame(
                {"value": [251.5 + i * 0.05 for i in range(80)]},
                index=idx,
            ),
            "T10Y2Y": pd.DataFrame(
                {"value": [0.30] * 30 + [0.30 - i * 0.008 for i in range(50)]},
                index=idx,
            ),
        }
        self.calls: list[tuple[str, date, date]] = []

    def fred_get_series(self, series_id: str, start: date, end: date):
        self.calls.append((series_id, start, end))
        if series_id in self.frames:
            return self.frames[series_id].copy()
        return ToolError(
            tool="fred",
            args={"series_id": series_id},
            message="series not in demo data",
        )


class DemoYahoo:
    def __init__(self) -> None:
        idx = pd.date_range("2018-06-01", periods=80, freq="W")
        self.frames: dict[str, pd.DataFrame] = {
            "SOXX": pd.DataFrame(
                {"Close": [180.0] * 30 + [180.0 - i * 0.45 for i in range(50)]},
                index=idx,
            ),
            "EEM": pd.DataFrame(
                {"Close": [44.0] * 30 + [44.0 - i * 0.06 for i in range(50)]},
                index=idx,
            ),
        }
        self.calls: list[tuple[str, date, date]] = []

    def yahoo_prices(self, ticker: str, start: date, end: date):
        self.calls.append((ticker, start, end))
        if ticker in self.frames:
            return self.frames[ticker].copy()
        return ToolError(
            tool="yahoo",
            args={"ticker": ticker},
            message="ticker not in demo data",
        )


# ---------------------------------------------------------------------------
# STUB MODEL
# ---------------------------------------------------------------------------


class DemoModel:
    """Routes by sentinel. Records every call so we can assert on the trace."""

    def __init__(self, *, verbose: bool = True) -> None:
        self.verbose = verbose
        self.calls: list[dict[str, Any]] = []

    def __call__(self, prompt: str, *, model: str, system: str = "") -> str:
        kind = self._classify(prompt)
        response = self._respond(kind, prompt)
        record = {
            "n": len(self.calls) + 1,
            "kind": kind,
            "model": model,
            "prompt_excerpt": _one_line(prompt, 220),
            "response_excerpt": _one_line(response, 160),
        }
        self.calls.append(record)
        if self.verbose:
            print(
                f"  [{record['n']:02d}] {kind:18s} "
                f"model={_short_model(model):8s} | {record['prompt_excerpt']}"
            )
            print(f"        -> {record['response_excerpt']}")
        return response

    @staticmethod
    def _classify(prompt: str) -> str:
        if "PROPOSE_CHILDREN" in prompt:
            return "propose_children"
        if "CHALLENGE_CANDIDATE" in prompt:
            return "challenge"
        if "STEP 1" in prompt:
            return "step1_data_refs"
        if "STEP 2" in prompt:
            return "step2_score_edge"
        return "unknown"

    def _respond(self, kind: str, prompt: str) -> str:
        if kind == "propose_children":
            return self._respond_propose(prompt)
        if kind == "challenge":
            return self._respond_challenge(prompt)
        if kind == "step1_data_refs":
            return self._respond_data_refs(prompt)
        if kind == "step2_score_edge":
            return self._respond_score(prompt)
        return "{}"

    def _respond_propose(self, prompt: str) -> str:
        # Layer 0 root vs layer 1 parents - inspect prompt for parent label.
        if "Section 301" in prompt and "layer 0" in prompt:
            return json.dumps(
                [
                    {
                        "label": "USD strengthens vs CNY",
                        "description": "Capital flight and safe-haven flows lift the broad USD index.",
                        "asset_class": "fx",
                        "mechanism": "Tariff escalation triggers safe-haven USD demand.",
                    },
                    {
                        "label": "Semi sector drawdown",
                        "description": "Semis with high China revenue exposure underperform on margin compression.",
                        "asset_class": "equities",
                        "mechanism": "Tariffs squeeze China-derived semi revenue, hitting the SOX.",
                    },
                    {
                        "label": "10y yield drifts lower",
                        "description": "Growth concerns and flight to quality push 10y yields down.",
                        "asset_class": "rates",
                        "mechanism": "Tariff-driven growth fears lift duration demand.",
                    },
                    {
                        "label": "HY credit spreads widen",
                        "description": "Risk-off rotation lifts high-yield option-adjusted spreads.",
                        "asset_class": "macro",
                        "mechanism": "Risk-off impulse repriced into corporate credit.",
                    },
                ]
            )
        if "USD strengthens" in prompt:
            return json.dumps(
                [
                    {
                        "label": "EM equities sell off",
                        "description": "EM equities decline as USD strength tightens dollar funding.",
                        "asset_class": "equities",
                        "mechanism": "Stronger USD raises dollar funding cost for EM corporates.",
                    },
                    {
                        "label": "DXY rises",  # restatement; should be challenged.
                        "description": "Dollar index rises.",
                        "asset_class": "fx",
                        "mechanism": "USD strengthens.",
                    },
                ]
            )
        if "Semi sector" in prompt:
            return json.dumps(
                [
                    {
                        "label": "Capex guides cut",
                        "description": "Semi-cap names cut capex guidance into Q4.",
                        "asset_class": "equities",
                        "mechanism": "Order pull-ins reverse, capex plans deferred.",
                    },
                    {
                        "label": "EM equity drag",  # near-redundant with the USD branch.
                        "description": "Asia-heavy EM equity benchmarks weaken alongside semis.",
                        "asset_class": "equities",
                        "mechanism": "Semis drag broader EM index weights.",
                    },
                ]
            )
        if "10y yield" in prompt or "HY credit" in prompt:
            return json.dumps(
                [
                    {
                        "label": "Curve flattens further",
                        "description": "2s10s flattens as growth fears dominate.",
                        "asset_class": "rates",
                        "mechanism": "Long-end rallies harder than the front end.",
                    }
                ]
            )
        return "[]"

    def _respond_challenge(self, prompt: str) -> str:
        if "DXY rises" in prompt:
            return json.dumps(
                {
                    "action": "drop",
                    "merge_with": None,
                    "reason": "Restatement of USD strengthens vs CNY.",
                }
            )
        if "EM equity drag" in prompt:
            return json.dumps(
                {
                    "action": "merge",
                    "merge_with": "EM equities sell off",
                    "reason": "Same observable variable as the existing sibling.",
                }
            )
        return json.dumps(
            {"action": "keep", "merge_with": None, "reason": "Distinct downstream variable."}
        )

    def _respond_data_refs(self, prompt: str) -> str:
        if "USD strengthens" in prompt:
            return json.dumps(
                {"fred_series": ["DTWEXBGS"], "tickers": [], "reasoning": "broad USD."}
            )
        if "Semi sector" in prompt:
            return json.dumps(
                {"fred_series": [], "tickers": ["SOXX"], "reasoning": "semi index."}
            )
        if "10y yield" in prompt or "Curve flattens" in prompt:
            return json.dumps(
                {"fred_series": ["DGS10", "T10Y2Y"], "tickers": [], "reasoning": "rates."}
            )
        if "HY credit" in prompt:
            return json.dumps(
                {"fred_series": ["BAMLH0A0HYM2"], "tickers": [], "reasoning": "HY OAS."}
            )
        if "EM equities" in prompt or "Capex guides" in prompt:
            return json.dumps(
                {"fred_series": [], "tickers": ["EEM"], "reasoning": "EM proxy."}
            )
        return json.dumps({"fred_series": [], "tickers": [], "reasoning": ""})

    def _respond_score(self, prompt: str) -> str:
        # Decide kept vs dropped based on the candidate label.
        if "Curve flattens" in prompt:
            payload = {
                "sensitivity": 0.65,
                "confidence": 0.6,
                "mechanism_refined": "Long end rallies on growth concerns, flattening 2s10s.",
                "supporting_data": [
                    {"series_id": "T10Y2Y", "peak_z": -2.4, "interpretation": "flattens 2.4 sigma."}
                ],
                "magnitude_estimate": -25.0,
                "keep": True,
                "keep_reason": "Two corroborating series with consistent sign.",
            }
        elif "USD strengthens" in prompt:
            payload = {
                "sensitivity": 0.7,
                "confidence": 0.7,
                "mechanism_refined": "Tariff escalation triggers safe-haven USD demand.",
                "supporting_data": [
                    {"series_id": "DTWEXBGS", "peak_z": 3.1, "interpretation": "USD up 3.1 sigma."}
                ],
                "magnitude_estimate": 0.04,
                "keep": True,
                "keep_reason": "Tight quantitative link.",
            }
        elif "Semi sector" in prompt:
            payload = {
                "sensitivity": 0.6,
                "confidence": 0.55,
                "mechanism_refined": "China-revenue exposure compresses semi margins.",
                "supporting_data": [
                    {"series_id": "SOXX", "peak_z": -2.6, "interpretation": "semi index draws down."}
                ],
                "magnitude_estimate": -0.18,
                "keep": True,
                "keep_reason": "Consistent direction with rough magnitude.",
            }
        elif "10y yield" in prompt:
            payload = {
                "sensitivity": 0.55,
                "confidence": 0.55,
                "mechanism_refined": "Tariff-driven growth fears lift duration demand.",
                "supporting_data": [
                    {"series_id": "DGS10", "peak_z": -2.8, "interpretation": "10y yield rallies."}
                ],
                "magnitude_estimate": -30.0,
                "keep": True,
                "keep_reason": "Clean directional move.",
            }
        elif "HY credit" in prompt:
            payload = {
                "sensitivity": 0.5,
                "confidence": 0.5,
                "mechanism_refined": "Risk-off impulse repriced into HY OAS.",
                "supporting_data": [
                    {"series_id": "BAMLH0A0HYM2", "peak_z": 2.4, "interpretation": "OAS widens."}
                ],
                "magnitude_estimate": 80.0,
                "keep": True,
                "keep_reason": "Single-series support, structural argument.",
            }
        elif "EM equities" in prompt or "Capex guides" in prompt or "EM equity drag" in prompt:
            payload = {
                "sensitivity": 0.45,
                "confidence": 0.4,
                "mechanism_refined": "USD funding tightening drags EM equities.",
                "supporting_data": [
                    {"series_id": "EEM", "peak_z": -2.0, "interpretation": "EEM weakens."}
                ],
                "magnitude_estimate": -0.1,
                "keep": True,
                "keep_reason": "One supporting ticker.",
            }
        elif "DXY rises" in prompt:
            # Will be dropped at challenge step anyway, but score it weakly.
            payload = {
                "sensitivity": 0.1,
                "confidence": 0.15,
                "mechanism_refined": "...",
                "supporting_data": [],
                "magnitude_estimate": None,
                "keep": False,
                "keep_reason": "Restatement, no incremental signal.",
            }
        else:
            payload = {
                "sensitivity": 0.3,
                "confidence": 0.3,
                "mechanism_refined": "default",
                "supporting_data": [],
                "magnitude_estimate": None,
                "keep": True,
                "keep_reason": "default",
            }
        return json.dumps(payload)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _one_line(text: str, length: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= length else flat[: length - 1] + "..."


def _short_model(model: str) -> str:
    if "opus" in model:
        return "opus"
    if "sonnet" in model:
        return "sonnet"
    if "haiku" in model:
        return "haiku"
    return model[:8]


def _print_subtree(graph: CausalGraph) -> None:
    print("\n  Nodes:")
    for nid, node in graph.nodes.items():
        marker = "*" if nid == graph.root else " "
        ac = node.asset_class or "-"
        print(
            f"    {marker} L{node.layer}  [{ac:11s}] {node.label}  ({nid})"
        )
    print("\n  Edges:")
    for e in graph.edges:
        src = graph.nodes[e.src].label
        dst = graph.nodes[e.dst].label
        cited = ", ".join(
            ev.ref for ev in e.supporting_data if ev.payload
        ) or "(no usable data)"
        print(
            f"    {src!r:34s} -> {dst!r:34s}  "
            f"sens={e.sensitivity:.2f}  conf={e.confidence:.2f}  cites=[{cited}]"
        )


def _print_call_summary(model: DemoModel, fred: DemoFRED, yahoo: DemoYahoo) -> None:
    by_kind: dict[str, int] = {}
    for c in model.calls:
        by_kind[c["kind"]] = by_kind.get(c["kind"], 0) + 1
    print("\n  Model call counts by kind:")
    for kind, n in by_kind.items():
        print(f"    {kind:20s} {n}")
    print(f"  FRED calls: {len(fred.calls)} (uniq series: {len({c[0] for c in fred.calls})})")
    print(f"  Yahoo calls: {len(yahoo.calls)} (uniq tickers: {len({c[0] for c in yahoo.calls})})")


# ---------------------------------------------------------------------------
# Demo runner
# ---------------------------------------------------------------------------


def run_demo(verbose: bool = True) -> CaseStudy:
    case_study = CaseStudy(
        name="2018 Section 301 tariffs",
        date_range=(date(2018, 7, 6), date(2019, 6, 30)),
        triggering_event="US imposes Section 301 tariffs on Chinese imports",
        macro_snapshot=MacroSnapshot(cpi_yoy=2.9, fed_funds=2.0, ten_year=2.85),
        similarity_score=0.7,
        subtree=CausalGraph(),
    )

    fred = DemoFRED()
    yahoo = DemoYahoo()
    tools = ToolBundle(fred=fred, yahoo=yahoo)
    model = DemoModel(verbose=verbose)

    # Both agent modules call _call_model; route both to the same demo model.
    import unittest.mock as mock

    if verbose:
        print(
            "\n=== Stage 3: Case Study Tree Construction ===\n"
            f"Case study: {case_study.name}\n"
            f"Window: {case_study.date_range[0]} to {case_study.date_range[1]}\n"
            f"Trigger: {case_study.triggering_event}\n"
        )
        print("Live trace of every model call (truncated):\n")

    with (
        mock.patch.object(tree_builder, "_call_model", model),
        mock.patch.object(sensitivity, "_call_model", model),
    ):
        result = tree_builder.build_subtree(
            case_study,
            tools=tools,
            max_layers=2,
            max_nodes=20,
        )

    if verbose:
        print("\n=== Result ===")
        _print_subtree(result.subtree)
        _print_call_summary(model, fred, yahoo)
        print()

    # Stash the model so the pytest test below can introspect it.
    result_meta = (result, model, fred, yahoo)
    return result_meta  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# pytest test that asserts on the demo trace
# ---------------------------------------------------------------------------


def test_demo_pipeline_executes_full_loop():
    result, model, fred, yahoo = run_demo(verbose=False)  # type: ignore[misc]

    # The graph has a root and at least two layers of children.
    layers = {n.layer for n in result.subtree.nodes.values()}
    assert {0, 1, 2}.issubset(layers), f"expected layers 0,1,2; got {layers}"

    # Every leaf has an asset class.
    leaves = tree_builder._leaves(result.subtree)
    assert leaves
    assert all(result.subtree.nodes[lid].asset_class for lid in leaves)

    # Challenger fired and produced at least one drop. Merging now happens
    # at the propose step (via existing_id in candidate JSON), so the challenger
    # no longer emits "merge" actions itself.
    challenges = [c for c in model.calls if c["kind"] == "challenge"]
    assert challenges
    actions_seen = " ".join(c["response_excerpt"] for c in challenges)
    assert '"drop"' in actions_seen, "expected at least one drop outcome"

    # All four call kinds were exercised at least once.
    kinds = {c["kind"] for c in model.calls}
    assert {
        "propose_children",
        "step1_data_refs",
        "step2_score_edge",
        "challenge",
    }.issubset(kinds)

    # Tools were actually invoked.
    assert fred.calls, "FRED stub never called"
    assert yahoo.calls, "Yahoo stub never called"

    # Every kept edge with confidence > 0.3 cites at least one usable Evidence.
    for e in result.subtree.edges:
        if e.confidence > 0.3:
            cited = [ev for ev in e.supporting_data if ev.payload]
            assert cited, (
                f"edge {e.src}->{e.dst} confidence={e.confidence} "
                "above 0.3 floor with no cited data"
            )


def test_demo_cycle_rejection_branch():
    """Direct unit-style check that _add_edge_if_dag refuses cycles.

    The demo model never produces cycles (fresh UUID node IDs), so we exercise
    the cycle path explicitly here."""
    g = CausalGraph()
    g.nodes["root"] = Node(id="root", label="root", description="", layer=0)
    g.nodes["mid"] = Node(id="mid", label="mid", description="", layer=1)
    g.root = "root"
    assert tree_builder._add_edge_if_dag(
        g, Edge(src="root", dst="mid", mechanism="", sensitivity=0.5, confidence=0.5)
    )
    cyclic = Edge(src="mid", dst="root", mechanism="", sensitivity=0.5, confidence=0.5)
    assert tree_builder._add_edge_if_dag(g, cyclic) is False
    assert len(g.edges) == 1


if __name__ == "__main__":
    run_demo(verbose=True)
    sys.exit(0)
