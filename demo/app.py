from __future__ import annotations

# Streamlit demo for the Policy Impact Scenario Mapper.
# Run: `streamlit run demo/app.py`

import tempfile
from pathlib import Path

import streamlit as st

from src.orchestrator import run_pipeline
from src.viz.graph import render_pyvis


def main() -> None:
    st.set_page_config(page_title="Policy Impact Scenario Mapper", layout="wide")
    st.title("Policy Impact Scenario Mapper")
    st.caption("Map a plain English policy event into a causal DAG with historical analogs and portfolio impacts.")

    with st.sidebar:
        dry_run = st.checkbox("Dry run (skip LLM/FRED calls)", value=False)
        max_first_order = st.slider("Max first-order nodes", 2, 8, 4)
        max_analogs = st.slider("Max analogs per node", 1, 4, 2)
        similarity_threshold = st.slider("Case study similarity threshold", 0.0, 1.0, 0.3, 0.05)

    event = st.text_area(
        "Policy event",
        value="25% tariff on Chinese semiconductors",
        height=100,
    )

    if not st.button("Map impact"):
        return

    with st.spinner("Running pipeline (this can take 30 to 90 seconds)..."):
        result = run_pipeline(
            event,
            dry_run=dry_run,
            max_first_order=max_first_order,
            max_analogs_per_node=max_analogs,
            similarity_threshold=similarity_threshold,
        )

    g = result.graph
    if not g.nodes:
        st.warning("Pipeline returned an empty graph. Check the dry-run flag, your API keys, and the event text.")
        return

    st.success(f"Built graph: {len(g.nodes)} nodes, {len(g.edges)} edges. Run id: {result.run_id}")

    tab_graph, tab_portfolio, tab_case_studies, tab_debates = st.tabs(
        ["Graph", "Portfolio impact", "Case studies", "Adversarial debates"]
    )

    with tab_graph:
        out_path = Path(tempfile.gettempdir()) / f"{result.run_id or 'graph'}.html"
        render_pyvis(g, out_path)
        st.components.v1.html(out_path.read_text(encoding="utf-8"), height=720, scrolling=True)

    with tab_portfolio:
        if not result.portfolio_impacts:
            st.info("No portfolio impacts emitted (no terminal nodes with classified asset class).")
        else:
            for impact in result.portfolio_impacts:
                with st.expander(f"{impact.asset_class.upper()}  -  {impact.direction}  ({impact.magnitude_label})"):
                    st.write(impact.summary)
                    st.write(f"**Confidence:** {impact.confidence:.2f}    **Time horizon:** {impact.time_horizon_days} days")
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
                kept = "kept" if cs.similarity_score >= similarity_threshold else "dropped"
                with st.expander(f"{cs.name}  -  similarity {cs.similarity_score:.2f} ({kept})"):
                    st.write(f"**Date range:** {cs.date_range[0]} to {cs.date_range[1]}")
                    st.write(f"**Trigger:** {cs.triggering_event}")
                    st.write(f"**Subtree:** {len(cs.subtree.nodes)} nodes, {len(cs.subtree.edges)} edges")
                    cmp_result = result.comparator_results.get(cs.name)
                    if cmp_result and cmp_result.diverging_dimensions:
                        st.write(f"**Most diverging dimensions:** {', '.join(cmp_result.diverging_dimensions)}")

    with tab_debates:
        if not result.debates:
            st.info("No debate transcripts.")
        else:
            for target_id, d in result.debates.items():
                outcome = "kept" if d.survives else "dropped"
                with st.expander(f"{target_id}  -  {outcome} (margin {d.margin:+.2f})"):
                    st.markdown(f"**Adversary** (score {d.critique.score:.2f}, {d.critique.attack_type or 'no type'})")
                    st.write(d.critique.counterargument)
                    st.markdown(f"**Defender** (score {d.rebuttal.score:.2f}, {d.rebuttal.defense_type or 'no type'})")
                    st.write(d.rebuttal.rebuttal)


if __name__ == "__main__":
    main()
