from __future__ import annotations

# Streamlit demo for the Policy Impact Scenario Mapper.
# Run: `streamlit run demo/app.py`

import tempfile
import warnings
from pathlib import Path

import matplotlib.pyplot as plt
import streamlit as st
import streamlit.components.v1 as components

from src.orchestrator import ProgressEvent, run_pipeline
from src.viz.graph import render_matplotlib, render_pyvis

# Quiet noisy upstream deprecation chatter so it does not clutter the app log.
warnings.filterwarnings("ignore", message=".*st.components.v1.html.*")
warnings.filterwarnings("ignore", message=".*possibly delisted.*")


def _embed_pyvis(html_path: Path) -> None:
    components.html(html_path.read_text(encoding="utf-8"), height=720, scrolling=True)


def _render_to_tmp(graph, name: str, run_id: str | None) -> Path:
    out = Path(tempfile.gettempdir()) / f"{run_id or 'graph'}_{name}.html"
    render_pyvis(graph, out)
    return out


def main() -> None:
    st.set_page_config(page_title="Policy Impact Scenario Mapper", layout="wide")
    st.title("Policy Impact Scenario Mapper")
    st.caption(
        "Map a plain English policy event into a causal DAG with historical "
        "analogs, adversarial debate, and portfolio impacts."
    )

    with st.sidebar:
        dry_run = st.checkbox("Dry run (skip LLM/FRED calls)", value=False)
        max_first_order = st.slider("Max first-order nodes", 2, 8, 4)
        max_analogs = st.slider("Max analogs per node", 1, 4, 2)
        similarity_threshold = st.slider(
            "Case study similarity threshold", 0.0, 1.0, 0.3, 0.05
        )

    event = st.text_area(
        "Policy event",
        value="25% tariff on Chinese semiconductors",
        height=100,
    )

    if not st.button("Map impact"):
        return

    # --- Live build section: trunk -> subtrees -> merged -> pruned ---------
    st.subheader("Live build")
    st.caption("Watch the trunk, each case-study subtree, the merged graph, and the pruned result fill in as the pipeline runs.")

    trunk_slot = st.empty()

    st.markdown("**Case study subtrees** (one per analog episode)")
    subtrees_container = st.container()
    subtree_slots: dict[str, object] = {}

    col_merge, col_prune = st.columns(2)
    with col_merge:
        st.markdown("**Merged graph (pre-prune)**")
        merged_slot = st.empty()
    with col_prune:
        st.markdown("**After pruning**")
        pruned_slot = st.empty()

    progress_box = st.status("Pipeline running...", expanded=True)

    def _show(slot, graph, title: str) -> None:
        fig = render_matplotlib(graph, title=title, figsize=(8, 5))
        if fig is not None:
            slot.pyplot(fig, clear_figure=True)
            plt.close(fig)

    def on_progress(ev: ProgressEvent) -> None:
        prefix = {
            "stage_start": "## ",
            "stage_complete": "## ✓ ",
            "stage_failed": "## ✗ ",
            "first_order_emitted": "- ",
            "analog_search_start": "- ",
            "analog_search_complete": "  - ",
            "case_study_started": "  - ",
            "case_study_built": "    - ",
            "debate_complete": "  - ",
            "comparator_result": "- ",
            "subtree_attached": "- ",
            "subtree_skipped": "- ",
            "merged_graph_built": "- ",
            "edge_pruned": "  - ",
            "subtree_dropped": "  - ",
            "node_orphaned": "  - ",
            "pruning_summary": "- ",
            "portfolio_impact_emitted": "- ",
        }.get(ev.kind, "- ")
        with progress_box:
            st.markdown(f"{prefix}{ev.message}")

        # Live diagram updates ---------------------------------------------
        if ev.kind == "stage_complete" and ev.data.get("stage") == 1:
            trunk = ev.data.get("trunk")
            if trunk:
                _show(trunk_slot, trunk, "Trunk: root + first-order nodes")
        elif ev.kind == "case_study_built":
            cs_id = ev.data.get("case_study_id")
            subtree = ev.data.get("subtree")
            name = ev.data.get("name", cs_id)
            parent = ev.data.get("first_order_label", "?")
            if cs_id and subtree and cs_id not in subtree_slots:
                with subtrees_container:
                    slot = st.empty()
                    subtree_slots[cs_id] = slot
                _show(subtree_slots[cs_id], subtree, f"{name}\n(parent: {parent})")
        elif ev.kind == "merged_graph_built":
            merged = ev.data.get("merged_graph")
            if merged:
                _show(merged_slot, merged, "Merged (pre-prune)")
        elif ev.kind == "stage_complete" and ev.data.get("stage") == 7:
            pruned = ev.data.get("pruned_graph")
            if pruned:
                _show(pruned_slot, pruned, "After pruning")

    with st.spinner("Running pipeline (this can take 30 to 90 seconds)..."):
        result = run_pipeline(
            event,
            dry_run=dry_run,
            max_first_order=max_first_order,
            max_analogs_per_node=max_analogs,
            similarity_threshold=similarity_threshold,
            on_progress=on_progress,
        )

    g = result.graph
    if not g.nodes:
        progress_box.update(state="error", label="Pipeline returned an empty graph")
        st.warning(
            "Pipeline returned an empty graph. Check the dry-run flag, your API "
            "keys, and the event text."
        )
        return

    progress_box.update(
        state="complete",
        label=f"Pipeline complete: {len(g.nodes)} nodes, {len(g.edges)} edges",
    )

    st.success(
        f"Built graph: {len(g.nodes)} nodes, {len(g.edges)} edges. "
        f"Run id: {result.run_id}"
    )

    # --- Final tabs (interactive pyvis + details) --------------------------
    tab_final, tab_pre_prune, tab_per_cs, tab_portfolio, tab_case_studies, tab_debates = st.tabs(
        [
            "Final graph (interactive)",
            "Pre-pruning graph (interactive)",
            "Per case study",
            "Portfolio impact",
            "Case studies",
            "Adversarial debates",
        ]
    )

    with tab_final:
        st.markdown(
            f"**{len(g.nodes)}** nodes, **{len(g.edges)}** edges, "
            f"**{sum(1 for cs in result.case_studies if cs.similarity_score >= similarity_threshold)}** "
            "case studies kept."
        )
        path = _render_to_tmp(g, "final", result.run_id)
        _embed_pyvis(path)

    with tab_pre_prune:
        if result.pre_prune_graph is None:
            st.info("No pre-prune snapshot available (e.g. dry run).")
        else:
            pre = result.pre_prune_graph
            n_dropped_edges = len(pre.edges) - len(g.edges)
            n_dropped_nodes = len(pre.nodes) - len(g.nodes)
            st.markdown(
                f"Before pruning: **{len(pre.nodes)}** nodes, **{len(pre.edges)}** edges. "
                f"After pruning: dropped **{n_dropped_nodes}** nodes and **{n_dropped_edges}** edges."
            )
            path = _render_to_tmp(pre, "pre_prune", result.run_id)
            _embed_pyvis(path)

            with st.expander("What was dropped, and why"):
                pruning_events = [
                    ev for ev in result.progress_events
                    if ev.kind in {"edge_pruned", "subtree_dropped", "node_orphaned"}
                ]
                if not pruning_events:
                    st.write("Nothing was pruned.")
                else:
                    for ev in pruning_events:
                        st.write(f"- **{ev.kind}**: {ev.message}")

    with tab_per_cs:
        if not result.case_studies:
            st.info("No case studies attached.")
        else:
            st.caption(
                "Each case study's subtree was built independently from its FRED "
                "analog episode, then attached under its first-order parent in stage 7."
            )
            for cs in result.case_studies:
                kept = (
                    "kept"
                    if cs.similarity_score >= similarity_threshold
                    else "dropped at similarity gate"
                )
                fo_id = result.case_study_to_first_order.get(cs.id, "?")
                fo_node = g.nodes.get(fo_id) or (
                    result.pre_prune_graph and result.pre_prune_graph.nodes.get(fo_id)
                )
                fo_label = fo_node.label if fo_node else fo_id
                with st.expander(
                    f"{cs.name}  -  similarity {cs.similarity_score:.2f}  ({kept})  -  parent: {fo_label}"
                ):
                    st.write(f"**Date range:** {cs.date_range[0]} to {cs.date_range[1]}")
                    st.write(f"**Trigger:** {cs.triggering_event}")
                    st.write(
                        f"**Subtree:** {len(cs.subtree.nodes)} nodes, "
                        f"{len(cs.subtree.edges)} edges"
                    )
                    cmp_result = result.comparator_results.get(cs.id)
                    if cmp_result and cmp_result.diverging_dimensions:
                        st.write(
                            f"**Most diverging dimensions:** "
                            f"{', '.join(cmp_result.diverging_dimensions)}"
                        )
                    if cs.subtree.nodes:
                        path = _render_to_tmp(cs.subtree, f"cs_{cs.id}", result.run_id)
                        _embed_pyvis(path)

    with tab_portfolio:
        if not result.portfolio_impacts:
            st.info(
                "No portfolio impacts (no terminal nodes with classified asset class)."
            )
        else:
            for impact in result.portfolio_impacts:
                with st.expander(
                    f"{impact.asset_class.upper()}  -  {impact.direction}  ({impact.magnitude_label})"
                ):
                    st.write(impact.summary)
                    st.write(
                        f"**Confidence:** {impact.confidence:.2f}    "
                        f"**Time horizon:** {impact.time_horizon_days} days"
                    )
                    if impact.tickers:
                        st.write(f"**Candidate tickers:** {', '.join(impact.tickers)}")
                    if impact.key_drivers:
                        st.write(f"**Key drivers:** {', '.join(impact.key_drivers)}")
                    if impact.offsets:
                        st.write(f"**Offsets:** {', '.join(impact.offsets)}")

    with tab_case_studies:
        if not result.case_studies:
            st.info("No case studies attached.")
        else:
            for cs in result.case_studies:
                kept = (
                    "kept"
                    if cs.similarity_score >= similarity_threshold
                    else "dropped"
                )
                with st.expander(
                    f"{cs.name}  -  similarity {cs.similarity_score:.2f} ({kept})"
                ):
                    st.write(f"**Date range:** {cs.date_range[0]} to {cs.date_range[1]}")
                    st.write(f"**Trigger:** {cs.triggering_event}")
                    cmp_result = result.comparator_results.get(cs.id)
                    if cmp_result:
                        st.write(f"**Distances:**")
                        st.json(cmp_result.distances)

    with tab_debates:
        if not result.debates:
            st.info("No debate transcripts.")
        else:
            for target_id, d in result.debates.items():
                outcome = "kept" if d.survives else "dropped"
                with st.expander(
                    f"{target_id}  -  {outcome}  (margin {d.margin:+.2f})"
                ):
                    st.markdown(
                        f"**Adversary** (score {d.critique.score:.2f}, "
                        f"{d.critique.attack_type or 'no type'})"
                    )
                    st.write(d.critique.counterargument)
                    st.markdown(
                        f"**Defender** (score {d.rebuttal.score:.2f}, "
                        f"{d.rebuttal.defense_type or 'no type'})"
                    )
                    st.write(d.rebuttal.rebuttal)


if __name__ == "__main__":
    main()
