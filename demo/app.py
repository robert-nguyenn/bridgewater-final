from __future__ import annotations

# Streamlit demo. Run: `streamlit run demo/app.py`
# TODO(integration): wire run_pipeline output to viz.render_pyvis and embed.

import streamlit as st

from src.orchestrator import run_pipeline


def main() -> None:
    st.set_page_config(page_title="Policy Impact Scenario Mapper", layout="wide")
    st.title("Policy Impact Scenario Mapper")
    event = st.text_area(
        "Policy event",
        value="25% tariff on Chinese semiconductors",
        height=100,
    )
    if st.button("Map impact"):
        with st.spinner("Mapping..."):
            graph = run_pipeline(event, dry_run=True)
        st.success(f"Built graph with {len(graph.nodes)} nodes, {len(graph.edges)} edges.")
        st.json({"nodes": list(graph.nodes), "edges": [e.__dict__ for e in graph.edges]})


if __name__ == "__main__":
    main()
