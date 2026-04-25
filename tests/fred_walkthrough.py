"""Live walkthrough of the FRED tool layer and the full SensitivityAgent loop.

Run:
    python -m tests.fred_walkthrough

Prints, in order:
    [1] A raw FRED fetch so you can see the actual data coming back.
    [2] The summary statistics that are fed to the model (the agent never sees
        the raw series; it sees these six numbers).
    [3] A macro_snapshot at a recent date.
    [4] fred_find_extrema episodes on 10y yield over 25 years.
    [5] One full live SensitivityAgent.score_edge call: real Anthropic +
        real FRED. Shows the prompts sent, the data pulled, and the score.

Requires FRED_API_KEY (steps 1-5) and ANTHROPIC_API_KEY (step 5 only)."""

from __future__ import annotations

import json
import sys
from datetime import date

import pandas as pd

from src.agents import sensitivity
from src.config import ANTHROPIC_API_KEY, FRED_API_KEY, MODEL_FAST
from src.tools import fred as fred_tool
from src.tools import make_default_tools
from src.types import (
    CaseStudy,
    CausalGraph,
    MacroSnapshot,
    Node,
    ToolError,
)


SECTION = "=" * 78


def banner(text: str) -> None:
    print(f"\n{SECTION}\n{text}\n{SECTION}")


def show(df: pd.DataFrame, n: int = 5) -> None:
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.width", 120)
    print(f"  shape: {df.shape}")
    print(f"  index: {df.index.dtype} from {df.index.min()} to {df.index.max()}")
    print(f"  columns: {list(df.columns)}")
    print(f"\n  head({n}):")
    print(df.head(n).to_string())
    print(f"\n  tail({n}):")
    print(df.tail(n).to_string())
    s = df.iloc[:, 0].dropna()
    print(f"\n  stats: min={s.min():.4f}  max={s.max():.4f}  "
          f"mean={s.mean():.4f}  std={s.std():.4f}  n={len(s)}")


def step_1_raw_fetch() -> None:
    banner("[1] RAW FRED FETCH: DGS10 over the 2018 Section 301 tariff window")
    print("Calling: fred_get_series('DGS10', 2018-06-06, 2019-09-30)")
    df = fred_tool.fred_get_series("DGS10", date(2018, 6, 6), date(2019, 9, 30))
    if isinstance(df, ToolError):
        print(f"  ToolError: {df.message}")
        sys.exit(1)
    show(df)


def step_2_summary_stats() -> None:
    banner("[2] SUMMARY STATS: what the agent actually reasons over")
    t0 = date(2018, 7, 6)   # tariff announcement
    t1 = date(2019, 6, 30)
    print(f"Case study window: {t0} to {t1}")
    print("Series under inspection: DTWEXBGS (USD broad), DGS10 (10y), "
          "BAMLH0A0HYM2 (HY OAS)")
    print()
    for sid in ("DTWEXBGS", "DGS10", "BAMLH0A0HYM2"):
        df = fred_tool.fred_get_series(sid, date(2018, 6, 6), date(2019, 9, 30))
        if isinstance(df, ToolError):
            print(f"  {sid}: ToolError {df.message}")
            continue
        stats = sensitivity._summarize_series(df, t0, t1)
        print(f"  {sid}:")
        for k, v in stats.items():
            print(f"      {k:24s} = {v}")


def step_3_macro_snapshot() -> None:
    banner("[3] macro_snapshot at 2024-06-30")
    snap = fred_tool.macro_snapshot(date(2024, 6, 30))
    if isinstance(snap, ToolError):
        print(f"  ToolError: {snap.message}")
        return
    for field in (
        "cpi_yoy", "core_pce_yoy", "fed_funds", "ten_year",
        "dxy", "unemployment", "real_gdp_yoy",
    ):
        val = getattr(snap, field)
        formatted = f"{val:.3f}" if isinstance(val, float) else str(val)
        print(f"  {field:18s} = {formatted}")


def step_4_extrema() -> None:
    banner("[4] fred_find_extrema: 10y yield z>=2.5 over rolling 60d, since 2000")
    eps = fred_tool.fred_find_extrema(
        "DGS10",
        threshold_zscore=2.5,
        window=60,
        history_start=date(2000, 1, 1),
        history_end=date(2024, 1, 1),
    )
    if isinstance(eps, ToolError):
        print(f"  ToolError: {eps.message}")
        return
    print(f"  found {len(eps)} episodes")
    for ep in eps[:15]:
        sign = "+" if ep.magnitude > 0 else "-"
        print(
            f"    {ep.start} to {ep.end}    "
            f"peak z = {sign}{abs(ep.magnitude):.2f}"
        )
    if len(eps) > 15:
        print(f"    ... and {len(eps) - 15} more")


def step_5_live_sensitivity() -> None:
    banner("[5] LIVE SensitivityAgent.score_edge: real Anthropic + real FRED")
    if not ANTHROPIC_API_KEY:
        print("  ANTHROPIC_API_KEY is not set. Skipping.")
        return

    # Patch _call_model so we can print the prompt/response pair.
    captured: list[dict] = []
    original = sensitivity._call_model

    def traced(prompt: str, *, model: str, system: str = "") -> str:
        kind = (
            "STEP_1_propose_data_refs" if "STEP 1" in prompt
            else "STEP_2_score_edge"
        )
        print(f"\n  --- model call [{kind}] (model={model}) ---")
        print("  PROMPT (first 800 chars):")
        for line in prompt[:800].splitlines():
            print(f"    {line}")
        if len(prompt) > 800:
            print(f"    ... ({len(prompt) - 800} more chars)")
        response = original(prompt, model=model, system=system)
        print("  RESPONSE:")
        for line in response.splitlines():
            print(f"    {line}")
        captured.append({"kind": kind, "prompt": prompt, "response": response})
        return response

    sensitivity._call_model = traced
    try:
        case = CaseStudy(
            name="2018 Section 301 tariffs",
            date_range=(date(2018, 7, 6), date(2019, 6, 30)),
            triggering_event="US imposes Section 301 tariffs on Chinese imports",
            macro_snapshot=MacroSnapshot(),
            similarity_score=0.7,
            subtree=CausalGraph(),
        )
        parent = Node(
            id="root",
            label="US imposes Section 301 tariffs on Chinese imports",
            description="July 2018 announcement of Section 301 tariffs on China.",
            layer=0,
        )
        candidate = Node(
            id="cand",
            label="10y UST yield drifts lower",
            description=(
                "Growth concerns and a flight-to-quality bid push the 10y yield "
                "lower through Q4 2018."
            ),
            layer=1,
        )
        mechanism = (
            "Tariff escalation tightens financial conditions, marking down growth "
            "expectations and lifting duration demand."
        )

        tools = make_default_tools()
        result = sensitivity.score_edge(
            parent=parent,
            candidate=candidate,
            mechanism=mechanism,
            case_study=case,
            tools=tools,
            model=MODEL_FAST,
        )
    finally:
        sensitivity._call_model = original

    print("\n  --- final EdgeScore ---")
    print(f"    sensitivity:        {result.sensitivity:.2f}")
    print(f"    confidence:         {result.confidence:.2f}")
    print(f"    keep:               {result.keep}")
    print(f"    keep_reason:        {result.keep_reason}")
    print(f"    mechanism_refined:  {result.mechanism_refined}")
    print(f"    magnitude_estimate: {result.magnitude_estimate}")
    print(f"    supporting_data ({len(result.supporting_data)} entries):")
    for ev in result.supporting_data:
        if ev.payload:
            print(f"      [{ev.kind}] {ev.ref}  payload={ev.payload}")
        else:
            print(f"      [{ev.kind}] {ev.ref}  note={ev.note}")


def main() -> int:
    if not FRED_API_KEY:
        print("FRED_API_KEY is not set in .env. Aborting.")
        return 1
    step_1_raw_fetch()
    step_2_summary_stats()
    step_3_macro_snapshot()
    step_4_extrema()
    step_5_live_sensitivity()
    banner("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
