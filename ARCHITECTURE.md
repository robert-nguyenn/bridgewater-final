# Architecture: Policy Impact Scenario Mapper

This is the end-to-end runbook for what the system does, which agent produces which part of the output, and how the causal tree is assembled. Written for someone who has read [CLAUDE.md](CLAUDE.md) and now needs to know exactly where each node and edge comes from.

## What it does, in one paragraph

A user types a plain-English policy event ("25% tariff on Chinese semiconductors"). The system produces a causal DAG that traces the event through affected economic channels to specific markets. Every edge has a sensitivity score, a confidence score, and a citation. Every first-order effect has 1 to 3 historical analog case studies attached as subtrees, each with a then-vs-now macro similarity score. An adversarial debate runs on every edge; losers are pruned. Surviving terminals are mapped to per-asset-class portfolio impacts. The system does not pick direction on broad indices; the value is in mapping what is affected, through which mechanism, how sensitive each link is, and how today's macro conditions differ from the closest historical precedent.

## Anatomy of the output tree

```
                          [root: event text]                 ← layer 0 (synthetic, orchestrator)
                         /        |        \
                    [FO 1]      [FO 2]      [FO 3]           ← layer 1, IdeaAgent
                       |           |
              ┌────────┴───┐       │
              ▼            ▼       ▼
          [analog A]   [analog B]  [analog C]                ← bridge edges added by orchestrator
              │            │           │                       (mechanism: "historical analog: <name>")
              ▼            ▼           ▼
         [subtree     [subtree     [subtree
          root]         root]        root]                   ← layer 2+, TreeBuilder per analog
              │            │           │
              ▼            ▼           ▼
        [downstream  [downstream  [downstream                 ← deeper layers, TreeBuilder
         variables]   variables]   variables]
              │            │           │
              ▼            ▼           ▼
        [terminals]  [terminals]  [terminals]                 ← portfolio mapping endpoints
```

A `Node` carries: `id`, `label`, `description`, `layer`, `asset_class` (optional), `magnitude_estimate` (optional), `evidence`.
An `Edge` carries: `id`, `src`, `dst`, `mechanism` (one-sentence prose), `sensitivity` ∈ [0, 1], `confidence` ∈ [0, 1], `supporting_data`, `adversary_notes`.

## End-to-end pipeline

The orchestrator ([src/orchestrator.py](src/orchestrator.py)) sequences the stages below. Stages 4 (LogicVerifier) and 8 (second TreeBuilder pass) are out of the pipeline path per CLAUDE.md's scope-cut order; both are individually callable. Stage 10 (ScenarioAgent) is stretch; it runs standalone and feeds back into stage 1 if invoked.

### Stage 1 — IdeaAgent: first-order generation

**Input:** plain-English `event` string.
**Output:** 3 to 8 layer-1 `Node` objects.

For "25% tariff on Chinese semiconductors", IdeaAgent might emit:
- USD strengthens vs CNY (`asset_class=fx`, `magnitude_estimate=0.04`)
- Chip ASP rises in US (`equities`, `+0.12`)
- China retaliation via critical minerals (`commodities`)
- Fed wait-and-see on inflation impulse (`rates`)

**Diversity rule (in [prompts/idea.md](prompts/idea.md)):** must cover ≥ 3 of 4 channels: real economy, financial conditions, policy reaction, second-order/counter-party.

**What lands in the tree:** the orchestrator wraps the result in a `root` node (layer 0, label = event text) and adds one edge per first-order node, mechanism `"event triggers <label>"`, with deliberately weak prior scores (sensitivity=0.5, confidence=0.4). These weak priors exist because the first link is structurally an LLM guess; downstream Adversary can downweight them.

**Tools used:** none. IdeaAgent uses Anthropic tool-use (`submit_first_order_nodes`) for structured output.

### Stage 2 — AnalogSearchAgent: historical analog discovery

**Input:** one first-order `Node` (the agent runs once per FO node).
**Output:** list of `Episode` objects, capped at `k`.

Three internal steps:

1. **PLAN_SERIES (LLM).** Pick the FRED series ID that best proxies the Node's variable (e.g. "Oil prices rise" → `DCOILWTICO`). Plus tuning: `threshold_zscore`, `window_obs`, `lookback_years`, `direction` (`up`/`down`/`either`).
2. **Spike detection (deterministic).** Calls `tools.fred.fred_find_extrema(series_id, threshold_zscore, window_obs, ...)`. Pulls history, computes rolling z-score, finds runs above threshold, groups by `min_episode_gap_days`.
3. **LABEL_EPISODES (LLM).** Names the candidate event for each episode ("Russia invades Ukraine", "2008 GFC oil unwind"). Labels are downstream context, not load-bearing.

**What lands in the tree:** nothing yet — Episodes feed stage 3.

**Tools used:** `tools.fred.fred_find_extrema` (which itself wraps `fred_get_series`).

### Stage 3 — TreeBuilder + Sensitivity: case-study subtree construction

For each (first-order node, Episode) pair (capped at `max_analogs_per_node`), the orchestrator first wraps the Episode into a `CaseStudy`:

```python
CaseStudy(
    name=ep.candidate_event,
    date_range=(ep.start, ep.end),
    triggering_event=ep.candidate_event,
    macro_snapshot=tools.fred.macro_snapshot(ep.start),  # FRED bundle at episode start
    similarity_score=0.0,                                 # filled in stage 6
    subtree=CausalGraph(),                                # populated below
)
```

Then **TreeBuilderAgent.build_subtree** expands the case study into a 2- to 3-layer DAG. The expansion loop, layer by layer:

For each parent in the previous layer:
- **PROPOSE_CHILDREN (LLM).** Name 3 to 5 candidate downstream nodes with mechanisms. Diverse asset classes per CLAUDE.md.
- **CHALLENGE_CANDIDATE (LLM, per candidate).** Decide `keep` / `drop` / `merge` against existing siblings. Drops restatements, wrong-asset-class labels, and tautologies.
- For each surviving candidate, the SensitivityAgent path runs:
  - **PROPOSE_DATA_REFS (LLM).** Which FRED series and tickers should move if this edge holds?
  - **Tool calls.** `tools.fred.fred_get_series` and `tools.yahoo.yahoo_prices` over the case-study window.
  - **summarize_series (deterministic).** Compute `pre_event_mean`, `post_event_mean`, `peak_deviation`, `peak_z`, `time_to_peak_days`.
  - **SCORE_EDGE (LLM).** Grade `sensitivity` and `confidence` from the summary stats. Citations required for any score > 0.3 (CLAUDE.md hard rule).
- **DAG check.** `_add_edge_if_dag` runs `networkx.is_directed_acyclic_graph` after candidate insertion. Rejected if it creates a cycle.

Stop conditions: `max_layers`, `max_nodes`, or layer max-confidence falls below `LAYER_CONFIDENCE_FLOOR`.

**What lands in the tree:** every CaseStudy now has a populated `.subtree` — its own internal CausalGraph rooted at the historical trigger.

**Tools used:** `tools.fred.fred_get_series`, `tools.yahoo.yahoo_prices` (when tickers are proposed).

### Stage 4 — LogicVerifier (skipped in pipeline, callable standalone)

`logic_verifier.run(chain, nodes=...)` runs a 4-pass Lean-style check on a causal chain (decompose → local validity → cross-step consistency → categorize failure into one of 8 categories) plus a structural score-vs-evidence consistency check that flags edges where `confidence > 0.3` but no evidence is cited. Skipped per CLAUDE.md scope cut order.

### Stage 5 — Adversary + Defender: adversarial debate per edge

`run_adversarial_debate(graph, ...)` is invoked twice: once on the trunk graph, then once per case-study subtree. For each `Edge`:

1. **AdversaryAgent.run(edge, nodes=...).** Argue against with one of six attack types:
   - `counter_example` (a past episode where the link failed)
   - `structural_objection` (channel broken / no longer exists)
   - `magnitude_doubt` (score too high relative to evidence)
   - `transmission_break` (intermediate variable currently absent)
   - `regime_mismatch` (today's regime breaks the historical analog)
   - `precondition_failure` (an unstated precondition is unmet)

   Returns `Critique(target_id, counterargument, score, attack_type, cited_evidence)`. Higher score = stronger case to remove.

2. **DefenderAgent.run(edge, critique, nodes=...).** Respond with one of six defense types:
   - `counter_evidence` (episodes contradicting the adversary)
   - `precondition_holds` (the unstated condition is in fact present)
   - `magnitude_robust` (link held across enough conditions that the edge case doesn't matter)
   - `regime_match` (today's regime does match the analog)
   - `mechanism_intact` (the channel is in fact intact)
   - `alternate_pathway` (concede primary, name another that gets there)

   Returns `Rebuttal(target_id, rebuttal, score, defense_type, cited_evidence)`. Higher score = stronger case to keep.

**What lands in the tree:** debate transcripts do not go into the graph itself. They live in `PipelineResult.debates: dict[edge_id, Debate]` and the Pruner reads them in stage 7. The demo's "Adversarial debates" tab renders them per edge.

**Tools used:** none — both agents reason from the artifact (mechanism + cited evidence). This is intentional per CLAUDE.md ownership ("must not edit … tools"); it forces the SensitivityAgent's citations to do the work.

### Stage 6 — MacroComparator: regime then-vs-now

For each case study, run `macro_comparator.run(case_study.macro_snapshot, today_snapshot)`. The comparator is **structural**, not LLM-based:

```
distances[field] = abs(then[field] - now[field]) / FIELD_SCALE[field]
similarity = exp(-mean(distances))
```

Field scales (chosen so a "meaningful regime gap" maps to ~1.0): cpi_yoy=2.0pp, core_pce_yoy=1.5pp, fed_funds=2.0pp, ten_year=1.5pp, dxy=10pt, unemployment=2.0pp, real_gdp_yoy=2.0pp. Fields where either snapshot is `None` are skipped (not penalized).

Identical → 1.0. Differing by ~1 scale on every field → ~0.37. Differing by ~2 scales on every field → ~0.13.

**What lands in the tree:** writes back `case_study.similarity_score`. Saves per-field distances to `PipelineResult.comparator_results[name]`. The Pruner uses similarity to keep or drop entire subtrees in stage 7.

**Tools used:** `tools.fred.macro_snapshot(today)` (called once by orchestrator before this stage, reused across all comparisons).

### Stage 7 — Tree merging + Pruner

Two sub-steps.

**Merge (orchestrator).** For each case study with `similarity_score >= similarity_threshold` (default 0.3):

1. Look up the case study's first-order parent via `case_study_to_first_order`.
2. Copy every node from `case_study.subtree.nodes` into the main `graph.nodes`.
3. Append every edge from `case_study.subtree.edges` to `graph.edges`.
4. Add a **bridge edge** `first_order_node → subtree_root` with `mechanism="historical analog: <case study name>"`, sensitivity=`similarity_score`, confidence=`similarity_score`. This is the structural attachment point between today's tree and the historical tree.

**Prune (PrunerAgent.run, structural, no LLM).** Three steps in order:

1. **Edge debate filter.** Drop any edge where `rebuttal.score - critique.score < debate_margin_threshold` (default 0.0 — defender wins ties).
2. **Subtree similarity filter.** For each case study with similarity below threshold, mark every node reachable from its subtree root as excluded.
3. **Reachability GC.** Keep only nodes reachable from `graph.root` via surviving edges, skipping excluded nodes. Drop everything else.

**What lands in the tree:** the merged-and-pruned `CausalGraph` is the system's primary output. This is what gets rendered.

### Stage 8 — Second TreeBuilder pass (skipped per scope cut)

Would expand surviving subtrees beyond their initial 2-3 layers, generating new downstream nodes inside attached case studies. First to cut after stretch scenario layer per CLAUDE.md.

### Stage 9 — PortfolioAgent: terminal-to-asset-class mapping

**Input:** terminal nodes of the merged graph (no outgoing edges, excluding root). Plus the full graph for inbound-edge stats per terminal.

The agent:
1. **_bucket_terminals.** Groups terminals by `asset_class`, dropping `unclassified`.
2. **_edge_stats.** For each terminal, computes `avg_confidence`, `avg_sensitivity`, `n_inbound` from inbound edges.
3. **submit_portfolio_impacts (LLM tool use).** For each asset class with at least one terminal, emit a `PortfolioImpact`:
   ```python
   PortfolioImpact(
       asset_class,                  # equities | futures | commodities | fx | rates | macro
       direction,                    # up | down | mixed
       summary,                      # one sentence stating the channel
       tickers,                      # list of liquid named instruments, capped at 6
       magnitude_label,              # small | moderate | large | unclear
       confidence,                   # in [0, 1], anchored on edge_stats
       key_drivers,                  # node ids most justifying the call
       offsets,                      # node ids pulling the opposite direction
       time_horizon_days,            # in [7, 365]
       contributing_nodes,           # superset of key_drivers + offsets
   )
   ```
4. **_post_process.** Drops hallucinated tickers / hallucinated node ids / unknown asset classes / classes with no input terminal. Collapses duplicates (first wins). Clamps `confidence` to [0, 1] and `time_horizon_days` to [7, 365]. Normalizes ticker case and strips empties.

**What lands in the tree:** nothing — `PortfolioImpact`s sit alongside the graph in `PipelineResult.portfolio_impacts`. The demo's "Portfolio impact" tab renders them.

**Tools used:** none directly (Yahoo is reserved for future ticker validation).

### Stage 10 — ScenarioAgent (stretch, callable standalone)

`scenario.run(seed_event, ...)` produces 4 to 8 tail policy scenarios anchored where possible to live Kalshi prediction-market prices. Each scenario has `probability_source ∈ {kalshi_exact, kalshi_adjusted, llm_calibrated}` with a strict delta-rationale rule when adjusting away from a market anchor (`|probability - kalshi_anchor_price| <= 0.15` and same side of 0.5). Output scenarios are designed to feed back into IdeaAgent as new seed events. Not wired into the main pipeline path.

## Where each part of the tree comes from, at a glance

| Tree element | Created by | Stage |
|---|---|---|
| `root` node (layer 0) | Orchestrator (synthetic, from event text) | 1 |
| First-order nodes (layer 1) | IdeaAgent | 1 |
| `root → first_order` edges (weak priors) | Orchestrator | 1 |
| Episode list per first-order node | AnalogSearchAgent | 2 |
| `CaseStudy` wrapper | Orchestrator (Episode + FRED `macro_snapshot`) | 3 |
| Case-study subtree nodes (layers 2+) | TreeBuilderAgent | 3 |
| Case-study subtree edges (mechanism prose) | TreeBuilderAgent | 3 |
| Edge `sensitivity`, `confidence`, `supporting_data` | SensitivityAgent (`score_edge`) | 3 |
| Debate transcripts (in `PipelineResult.debates`) | AdversaryAgent + DefenderAgent | 5 |
| `case_study.similarity_score` | MacroComparator | 6 |
| Bridge edges `first_order → subtree_root` | Orchestrator (after similarity gate) | 7 |
| Pruned graph (final output) | PrunerAgent | 7 |
| Portfolio impact summaries (alongside graph) | PortfolioAgent | 9 |
| Tail scenarios (stretch, alongside) | ScenarioAgent | 10 |

## Tool usage by agent

| Tool function | Used by |
|---|---|
| `tools.fred.fred_find_extrema` | AnalogSearch (spike detection) |
| `tools.fred.fred_get_series` | Sensitivity (data for scoring) |
| `tools.fred.macro_snapshot` | Orchestrator (CaseStudy snapshot at episode start; today's snapshot for MacroComparator) |
| `tools.yahoo.yahoo_prices` | Sensitivity (when tickers are proposed) |
| `tools.yahoo.yahoo_fundamentals` | Sensitivity (optional) |
| `tools.hf.hf_dataset_query` / `central_bank_search` / `news_search` | Scenario (news for tail-event grounding) |

IdeaAgent, Adversary, Defender, MacroComparator, Pruner, Portfolio, LogicVerifier do not call tools directly. They reason over structured data already in the graph.

## Data flow shape

```
event:str
   │
   ▼
IdeaAgent ────────────────────────────── list[Node]                        (stage 1)
   │
   ▼
graph = CausalGraph(root + first-order Nodes + weak-prior edges)
   │
   ▼
for each first_order_node:
    AnalogSearchAgent ─────────────── list[Episode]                        (stage 2)
    for each episode (capped at max_analogs_per_node):
        case_study = CaseStudy(macro_snapshot via FRED)
        TreeBuilderAgent.build_subtree ── case_study.subtree populated     (stage 3)
            └─ for each candidate edge: SensitivityAgent.score_edge
                                              ├─ FRED tool calls
                                              └─ Yahoo tool calls
   │
   ▼
run_adversarial_debate(graph) + per case_study.subtree                     (stage 5)
    AdversaryAgent ──── Critique
    DefenderAgent ───── Rebuttal
                        → dict[edge_id, Debate]
   │
   ▼
for each case_study:
    MacroComparator(case_study.macro_snapshot, today_snapshot)             (stage 6)
        case_study.similarity_score ← result.similarity
   │
   ▼
Merge surviving subtrees + add bridge edges                                (stage 7)
PrunerAgent(graph, debates, comparator) ── pruned CausalGraph
   │
   ▼
PortfolioAgent(terminals, graph) ────── list[PortfolioImpact]              (stage 9)
   │
   ▼
PipelineResult(graph, case_studies, portfolio_impacts, debates, comparator_results)
```

## Worked example trace

**Event:** "ECB launches emergency lending facility for southern European banks."

**Stage 1 (IdeaAgent).** 5 first-order nodes:
- `Peripheral sovereign spreads compress` (rates)
- `EUR/USD weakens` (fx)
- `Eurostoxx banks rally` (equities)
- `ECB balance sheet expansion` (macro)
- `Sovereign refinancing risk drops in periphery` (rates)

The orchestrator builds a `root` node from the event text and adds 5 weak-prior edges from `root` to each FO node.

**Stage 2 (AnalogSearch on `Peripheral sovereign spreads compress`).**
- PLAN_SERIES picks `BAMLEMHBHYCRPIUSOAS`, direction `down`, threshold 2.5σ.
- `fred_find_extrema` returns 4 episodes since 1980 where the spread compressed sharply.
- LABEL_EPISODES names them: 2011 LTRO (Dec 2011), 2012 OMT (Jul 2012), 2020 PEPP (Mar 2020), 2015 expanded APP.
- Top 2 by magnitude → 2011 LTRO and 2012 OMT.

**Stage 3 (TreeBuilder on the 2011 LTRO case study).**
- Case study root: `ECB introduces 3-year LTRO` (subtree-internal layer 0).
- Layer 1: `EUR/USD declines`, `Eurostoxx banks rally`, `Peripheral 10y yields fall`.
- Layer 2 under `Eurostoxx banks rally`: `Bank book values rise on collateral`, `Senior bank debt CDS narrows`.
- For each edge, SensitivityAgent pulls `BAMLH0A0HYM2`, `EUFN`, `DEXUSEU`, computes peak_z, scores sensitivity 0.7 and confidence 0.75 (per the worked example in [prompts/sensitivity.md](prompts/sensitivity.md)) with citations.

**Stage 5 (Adversarial debate, e.g., on `LTRO → Eurostoxx banks rally`).**
- Adversary (`regime_mismatch`): "2011 was peak Eurozone solvency crisis; today's banks are over-capitalized post-Basel III." Score 0.55.
- Defender (`mechanism_intact`): "the proximate channel is funding cost compression, which holds across both regimes." Score 0.6.
- Margin = +0.05 → kept. Transcript stored in `debates[edge_id]`.

**Stage 6 (MacroComparator, then=2011-12 vs now).**
- Distances: cpi_yoy 1.2, fed_funds 1.5, ten_year 0.4, unemployment 0.8, dxy 0.3.
- Mean = 0.84, similarity ≈ 0.43. Above threshold 0.3 → case study kept.
- Top 3 diverging dimensions: fed_funds, cpi_yoy, unemployment.

**Stage 7 (Merge + Prune).**
- LTRO subtree's nodes/edges copied into the main graph.
- Bridge edge `Peripheral sovereign spreads compress → ECB introduces 3-year LTRO` added with mechanism `"historical analog: 2011 LTRO"`, sensitivity=0.43, confidence=0.43.
- 2012 OMT subtree similarly attached (similarity 0.5).
- Adversary-won edges pruned. Orphaned subtree leaves GC'd.

**Stage 9 (Portfolio).**
- `equities`: up, tickers `[EUFN, SX7E, NVDA, SOXX]`, magnitude moderate, key_drivers from bank-equity terminals.
- `fx`: down on EUR, tickers `[EURUSD=X, DX=F]`, magnitude small.
- `rates`: down on peripheral spreads (`BAMLEMHBHYCRPIUSOAS`), magnitude moderate.

End user sees an interactive pyvis DAG with hover details, plus four tabs in [demo/app.py](demo/app.py): Graph, Portfolio impact, Case studies, Adversarial debates.

## Key design choices and their trade-offs

- **First-link speculation vs downstream defensibility.** The `root → first-order` edge is structurally an LLM guess. The orchestrator gives these weak prior scores (sensitivity 0.5, confidence 0.4) so downstream Adversary can downweight them. Confidence and rendering should reflect this asymmetry — first-link edges are advisory, downstream edges are empirical.
- **Sensitivity scores are agent-graded, not regression coefficients.** Mitigations: (a) evidence citations required for any score > 0.3, (b) AdversaryAgent runs on every edge with the transcript stored for audit, (c) the structural score-vs-evidence consistency check in LogicVerifier (callable but not in pipeline).
- **AnalogSearch on a single series is noisy.** V1 uses one series per first-order node. CLAUDE.md notes the long-term plan is multi-series matching plus regime tagging.
- **MacroComparator and Pruner are structural, not LLM-based.** Both inputs are numeric, both decisions are mechanical. An LLM here burns tokens for no judgment value.
- **Adversary cannot call tools.** It argues from priors and the artifact's cited evidence — per CLAUDE.md ownership, the agent "must not edit … tools." This forces value into the artifact and makes SensitivityAgent's citations carry weight at debate time.
- **Bridge edge sensitivity = similarity_score.** A natural choice: a case study with `similarity=0.43` should not contribute to today's tree more strongly than its regime match warrants. The Adversary can still attack the bridge.
- **Defender wins ties.** Default `debate_margin_threshold = 0.0`. Stricter pruning is a knob: pass `debate_margin_threshold=0.1` or higher to require the defender to clearly outargue the adversary.

## Where to look for what

| Concern | File |
|---|---|
| Pipeline sequencing, `PipelineResult` shape, debate runner | [src/orchestrator.py](src/orchestrator.py) |
| Type contracts (Node, Edge, CaseStudy, MacroSnapshot, CausalGraph, Episode, Evidence) | [src/types.py](src/types.py) |
| Per-agent logic | `src/agents/<name>.py` |
| Per-agent prompt | `prompts/<name>.md` |
| FRED, Yahoo, HF wrappers | `src/tools/<name>.py` |
| Disk cache decorator | [src/tools/cache.py](src/tools/cache.py) |
| Graph rendering (pyvis, graphviz) | [src/viz/graph.py](src/viz/graph.py) |
| Streamlit UI | [demo/app.py](demo/app.py) |
| Per-module smoke tests | `tests/test_<name>.py` |
| Shared agent helpers (JSON parsing, formatters, score clamping) | [src/agents/_common.py](src/agents/_common.py) |

## How to run

```bash
# Setup (one-time)
/usr/local/bin/python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,demo]"
cp .env.example .env  # fill ANTHROPIC_API_KEY, FRED_API_KEY, HF_TOKEN
huggingface-cli login

# Tests
pytest -q                # 155 passed, 2 skipped (live API smokes)
RUN_LIVE=1 pytest -q     # also runs live FRED, Yahoo, HF smokes

# CLI dry run
python -m src.orchestrator --event "..." --dry-run

# CLI real run (hits Anthropic + FRED + Yahoo + HF)
python -m src.orchestrator --event "ECB launches emergency lending facility for southern European banks"

# Streamlit demo
streamlit run demo/app.py
```
