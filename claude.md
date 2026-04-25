# CLAUDE.md

Guidance for Claude Code (and humans) working in this repo. Read this before writing code.

## What we're building

**Policy Impact Scenario Mapper.** Input: a rare or out of sample policy event in plain language (e.g. "25% tariff on Chinese semiconductors"). Output: a structured causal DAG mapping the event through affected economic channels to specific markets, with per edge sensitivity and confidence scores, grounded in FRED point estimates, company fundamentals, and historical analogs.

The tool does not make a directional call. The value is in comprehensively mapping what is affected, through which mechanism, how sensitive each link is, and how today's conditions differ from the closest historical precedent.

This is built for the Bridgewater AI Hackathon (Friday + Saturday). Ship over polish.

## Core thesis

Three things make this distinct from "ask Claude what happens after a tariff":

1. **Historical analogs come from data, not vibes.** For each proposed first order effect, we scan FRED series for past episodes where that variable spiked or dipped at comparable magnitude. Those episodes become case studies whose own causal trees we attach as subtrees.
2. **Every edge carries a sensitivity and confidence score** derived from agent reasoning over numerical evidence (FRED + Yahoo) plus textual evidence (central bank speeches, news).
3. **Adversarial pruning.** Adversary agents argue against each node and edge. Survivors go in. Macro condition comparison then prunes case studies that don't generalize to today.

## Pipeline (end to end)

```
[Plain English Event]
        │
        ▼
[1. First Order Node Generation]   IdeaAgent + Adversary
        │  (3 to 8 first order nodes)
        ▼
[2. Historical Analog Search]       AnalogSearchAgent
        │  for each first order node, scan FRED for matching spikes/dips
        │  return list of (date_range, series, magnitude, candidate_event)
        ▼
[3. Case Study Tree Construction]   TreeBuilderAgent + SensitivityAgent
        │  for each analog, build 2 to 3 layer causal subtree using
        │  FRED + Yahoo tool calls, attach sensitivity/confidence per edge
        ▼
[4. Logic Verification]             LogicVerifierAgent
        │  Lean style check that each chain is locally valid
        ▼
[5. Adversarial Debate]             AdversaryAgent vs DefenderAgent
        │  prune nodes/edges that lose
        ▼
[6. Macro Then vs Now Comparison]   MacroComparatorAgent
        │  drop case studies whose macro snapshot is too far from today
        ▼
[7. Tree Merging]                   Orchestrator
        │  attach surviving case study subtrees under their first order nodes
        │  prune the today tree where evidence collapses
        ▼
[8. Subtree Expansion]              TreeBuilderAgent (second pass)
        │  generate new nodes in attached subtrees and beyond first order
        ▼
[9. Portfolio Impact Layer]         PortfolioAgent
        │  map terminal nodes to equities, futures, commodities, FX
        ▼
[10. Tail Scenario Layer (stretch)] ScenarioAgent
            pull news, generate tail policy scenarios with probability,
            feed each back into stage 1
```

## Data sources

| Source | Use | Access |
|--------|-----|--------|
| FRED | macro series for analog scanning, sensitivity quantification, then/now snapshots | `fredapi` or HF mirror |
| defeatbeta / Yahoo Finance | company fundamentals, revenue breakdowns, prices | HuggingFace dataset |
| aufklarer/central-bank-communications, istat-ai/ECB-FED-speeches | central bank response evidence for edges touching policy reaction | HuggingFace dataset |
| fancyzhx/ag_news, dell-research-harvard/newswire | historical analog context, stretch tail scenarios | HuggingFace dataset |
| sovai/government_contracts | fiscal channel evidence | HuggingFace dataset |
| Perplexity API (optional) | live news for stretch scenario layer | gated, $50 min |

All HuggingFace datasets are loaded via `datasets.load_dataset` after `huggingface_hub` login. See `setup.md` for tokens.

## Key abstractions

Define these as dataclasses or pydantic models in `src/types.py`. Keep them stable, the rest of the code branches off them.

```python
class Node:
    id: str
    label: str                    # "USD strengthens", "Chip ASP +12%"
    description: str              # one or two sentences
    layer: int                    # 0 = root event, 1 = first order, 2+ = downstream
    asset_class: Optional[str]    # equities | futures | commodities | fx | rates | macro
    magnitude_estimate: Optional[float]  # signed, in natural units
    evidence: list[Evidence]      # FRED series refs, ticker refs, citations

class Edge:
    src: str                      # node id
    dst: str                      # node id
    mechanism: str                # short prose causal mechanism
    sensitivity: float            # [0, 1], how much dst moves per unit of src
    confidence: float             # [0, 1], how sure we are this edge exists
    supporting_data: list[Evidence]
    adversary_notes: Optional[str]  # what the adversary said, retained for transparency

class CaseStudy:
    name: str                     # "2018 Section 301 tariffs"
    date_range: tuple[date, date]
    triggering_event: str
    macro_snapshot: MacroSnapshot
    similarity_score: float       # vs today
    subtree: CausalGraph

class MacroSnapshot:
    # vector of conditions for then vs now comparison
    cpi_yoy: float
    core_pce_yoy: float
    fed_funds: float
    ten_year: float
    dxy: float
    unemployment: float
    real_gdp_yoy: float
    # extend as needed

class CausalGraph:
    nodes: dict[str, Node]
    edges: list[Edge]
    root: str
```

Use `networkx.DiGraph` internally for traversal and acyclicity checks. Wrap it so the rest of the code only touches `CausalGraph`.

## Agent roles

Each agent is a function that takes structured input and returns structured output. Keep prompts in `prompts/`, logic in `src/agents/`.

| Agent | Input | Output |
|-------|-------|--------|
| `IdeaAgent` | event text | list of candidate first order Nodes |
| `AnalogSearchAgent` | Node + FRED tool | list of historical episodes where the relevant series moved comparably |
| `TreeBuilderAgent` | seed Node + tools | partial CausalGraph (2 to 3 layers) |
| `SensitivityAgent` | Edge + data | (sensitivity, confidence, supporting_data) |
| `LogicVerifierAgent` | causal chain | pass/fail + reason. Think of it as Lean for macro: each step must follow from a stated premise plus a named mechanism |
| `AdversaryAgent` | Node or Edge | counterargument, score |
| `DefenderAgent` | Node or Edge + adversary critique | rebuttal, score |
| `MacroComparatorAgent` | two MacroSnapshots | similarity score + which dimensions differ most |
| `PrunerAgent` | graph + debate transcripts + comparator scores | pruned graph |
| `PortfolioAgent` | terminal nodes | per asset class impact summary |
| `ScenarioAgent` (stretch) | news corpus | list of (tail policy scenario, probability) |

Keep each agent under ~150 lines. If it's getting bigger, split.

## Tools (function calls Claude makes)

These are the surfaces agents call. Implement once in `src/tools/`, reuse everywhere.

- `fred_get_series(series_id, start, end) -> DataFrame`
- `fred_find_extrema(series_id, threshold_zscore, window) -> list[Episode]` — for analog search
- `yahoo_fundamentals(ticker, fields) -> dict`
- `yahoo_prices(ticker, start, end) -> DataFrame`
- `hf_dataset_query(repo_id, filter) -> rows` — generic accessor for the HF datasets
- `central_bank_search(query, date_range) -> list[Speech]`
- `news_search(query, date_range) -> list[Article]`
- `macro_snapshot(date) -> MacroSnapshot` — pulls a fixed bundle of FRED series at a date

Every tool returns a typed result and logs the call. Cache aggressively, we will hit the same FRED series many times.

## Sensitivity and confidence: how to score

Make these concrete so agents output comparable numbers.

**Sensitivity** in [0, 1]:
- 0.0 to 0.2: weak or contested historical co movement
- 0.2 to 0.5: directional co movement, magnitude varies
- 0.5 to 0.8: consistent directional and rough magnitude link across multiple episodes
- 0.8 to 1.0: tight quantitative link with clean transmission

**Confidence** in [0, 1]:
- 0.0 to 0.3: agent is reasoning from priors only
- 0.3 to 0.6: 1 to 2 supporting data points or one historical episode
- 0.6 to 0.85: multiple episodes plus structural argument
- 0.85 to 1.0: tight mechanism with quantitative support across episodes

Agents must cite the FRED series, ticker, or document that justifies any score above 0.3.

## Repo layout

```
.
├── CLAUDE.md                    # this file
├── README.md                    # short user facing version
├── pyproject.toml
├── .env.example
├── setup.md                     # HF token, FRED key, Anthropic key steps
├── src/
│   ├── types.py                 # Node, Edge, CaseStudy, MacroSnapshot, CausalGraph
│   ├── orchestrator.py          # the 10 stage pipeline
│   ├── agents/
│   │   ├── idea.py
│   │   ├── analog_search.py
│   │   ├── tree_builder.py
│   │   ├── sensitivity.py
│   │   ├── logic_verifier.py
│   │   ├── adversary.py
│   │   ├── defender.py
│   │   ├── macro_comparator.py
│   │   ├── pruner.py
│   │   ├── portfolio.py
│   │   └── scenario.py          # stretch
│   ├── tools/
│   │   ├── fred.py
│   │   ├── yahoo.py
│   │   ├── hf.py
│   │   └── cache.py
│   └── viz/
│       └── graph.py             # networkx + pyvis or graphviz export
├── prompts/                     # one .md per agent, version in git
├── notebooks/                   # exploration only, do not import from
├── tests/                       # smoke tests on tools, golden tests on small graphs
└── demo/
    └── app.py                   # Streamlit or Gradio frontend for Saturday demo
```

## Module ownership and parallel work

This codebase is built for **parallel work by multiple humans (and multiple Claude Code instances) without stepping on each other.** Each module is an isolated, composable unit with a typed interface defined in `src/types.py`. Each owner only edits files inside their assigned directory.

### The hard rule for Claude Code

When you launch a Claude Code session, scope it to one module from the table below. Tell Claude explicitly at the start of the session. Paste this template, filling in your module:

> You are working only on the `<module name>` module. You may edit files in `<allowed paths>` and read from `src/types.py` and any tool wrappers you depend on. Do not modify `src/types.py`, other agents, the orchestrator, or any module outside your scope. If you need a change in another module, leave a `# TODO(integration):` comment with what you need and stop. Run `pytest tests/test_<your_module>.py -x` before declaring anything done.

This keeps each Claude focused, prevents merge conflicts, and means any teammate can review a single PR without holding the whole repo in their head.

### Module boundaries

| Module | Owner edits | Reads from | Must not edit |
|--------|-------------|------------|---------------|
| **Types and orchestration** (lead/integrator) | `src/types.py`, `src/orchestrator.py`, `src/config.py` | everything | the trunk owns the trunk, no restriction |
| **Tools: FRED + cache** | `src/tools/fred.py`, `src/tools/cache.py`, `tests/test_fred.py` | `src/types.py` | agents, other tools, orchestrator |
| **Tools: Yahoo + HF** | `src/tools/yahoo.py`, `src/tools/hf.py`, `tests/test_yahoo.py`, `tests/test_hf.py` | `src/types.py` | agents, FRED tool, orchestrator |
| **Agents: IdeaAgent + AnalogSearch** | `src/agents/idea.py`, `src/agents/analog_search.py`, `prompts/idea.md`, `prompts/analog_search.md`, matching tests | `src/types.py`, tools | other agents |
| **Agents: TreeBuilder + Sensitivity** | `src/agents/tree_builder.py`, `src/agents/sensitivity.py`, matching prompts and tests | `src/types.py`, tools | other agents |
| **Agents: LogicVerifier + Adversary + Defender** | the three agent files, matching prompts and tests | `src/types.py` | other agents, tools |
| **Agents: MacroComparator + Pruner** | both agent files, matching prompts and tests | `src/types.py`, FRED tool | other agents |
| **Agents: Portfolio + Scenario (stretch)** | both agent files, matching prompts and tests | `src/types.py`, all tools | other agents |
| **Viz + Demo** | `src/viz/`, `demo/app.py` | `src/types.py` only | everything else |

For a 4 person team, suggested split: integrator + tools (1), idea/analog/tree/sensitivity (1), logic/adversary/defender/comparator/pruner (1), portfolio/viz/demo (1). For 5, split agents further.

### Contract first, code second

Before anyone writes an agent, the integrator nails down `src/types.py` and merges it. After that, **types are append only for the rest of the hackathon.** Adding a field is fine. Renaming or removing a field requires a team sync. Pin a Slack message with the current types so teammates can reference without pulling.

### How agents stay composable

Every agent is a pure(ish) function with this shape:

```python
def run(input: TypedInput, *, tools: ToolBundle, model: str) -> TypedOutput:
    ...
```

- No global state.
- No side effects beyond logging and tool calls.
- Tools are passed in, never imported at module top. This makes mocking trivial in tests.
- Inputs and outputs are dataclasses from `src/types.py`. If you find yourself returning a dict, stop and add a type.

If two agents need to share state, that state lives in the orchestrator, not in either agent.

### Smoke test contract per module

Each owner ships a `tests/test_<module>.py` that runs in under 30 seconds against cached fixtures. Fixtures live in `tests/fixtures/`. The integrator runs all smoke tests before any merge.

### When integration breaks

If the orchestrator can't wire two agents together (output of A doesn't fit input of B), the integrator owns the fix in `src/types.py` and notifies both agent owners. Agent owners do not edit each other's modules to "make it work."

## Conventions

- **Python 3.11+.** Type hints everywhere. `from __future__ import annotations`.
- **No silent failures.** Tool calls that miss return a typed `ToolError`, never a fake number.
- **Cache FRED responses to disk** keyed by (series_id, start, end). FRED is fast but we'll re run the pipeline a lot.
- **Every agent call is logged** to `logs/{run_id}/` with input, output, and prompt hash. Critical for the demo, mentors will ask "why this node".
- **Prompts live in `prompts/` as markdown.** Load them at runtime. Do not inline long prompts in Python.
- **Keep the model in one place.** `config.py` holds `MODEL = "claude-opus-4-5-20251101"` (or whichever Opus 4.7 string is current per the API console). Sonnet for cheap subtasks, Opus for the heavy reasoning agents (LogicVerifier, Adversary, MacroComparator).
- **No em dashes, en dashes, or hyphens used as dashes in any user facing prose.** Use commas or periods. Compound hyphens in code identifiers are fine.
- **DAG only.** Reject cycles at insertion time. `networkx.is_directed_acyclic_graph` after every merge.

## Build order (ship plan)

Roughly mapped to the hackathon checkpoints.

**Friday morning (design check in):**
1. Types nailed down (`src/types.py`).
2. FRED + Yahoo + HF tool wrappers working with caching.
3. `IdeaAgent` and `AnalogSearchAgent` producing sane output on the semiconductor tariff example.

**Friday afternoon (build check in):**
4. `TreeBuilderAgent` + `SensitivityAgent` producing a 2 layer subtree for one case study.
5. `MacroComparatorAgent` returning a scalar similarity for then vs today.
6. End to end pipeline runs on one example, even if rough.

**Friday evening:**
7. `AdversaryAgent` + `DefenderAgent` + `PrunerAgent` actually changing the graph.
8. `LogicVerifierAgent` catching at least one bad chain.
9. Visualization (graphviz or pyvis) renders the merged tree.

**Saturday morning (dry run):**
10. `PortfolioAgent` mapping terminal nodes to asset class impacts.
11. Streamlit/Gradio demo with text input and graph output.
12. Two more example events working end to end.

**Saturday afternoon (final demo):**
13. Polish viz, write demo script, decide on the one example we lead with.
14. Stretch: `ScenarioAgent` with news pull and probability scores.

If we hit Friday evening and step 7 isn't done, **cut the stretch scenario layer first, then the logic verifier, then the second TreeBuilder pass.** Do not cut adversarial debate, that is the differentiated story.

## What good looks like in the demo

- Researcher types: "ECB launches emergency lending facility for southern European banks."
- 30 to 90 seconds later, a DAG appears.
- First order nodes: peripheral spread compression, EUR weakness, bank equity rally, sovereign refinancing risk.
- Each first order node has a historical analog visibly attached (LTRO 2011, OMT 2012, PEPP 2020), with a then vs now similarity badge.
- Hover any edge, see sensitivity, confidence, and the FRED series or document that justified it.
- Terminal layer shows portfolio impact across asset classes.
- Mentor asks "why did you keep this edge", we click it, the adversary transcript and the supporting evidence are right there.

## What we are explicitly not doing

- Regression based forward forecasting. We use point estimates of historical episodes, not fitted models. Mentioned because someone will ask.
- Backtesting trade strategies. We map impact, we do not call direction.
- LLM as oracle. Every numeric claim ties to a tool call result.
- Chasing more datasets before the core works. Initial project first, then expand.

## Design risks (acknowledged)

These came up during planning. Captured here so we don't relitigate, and so we recognize them when they bite us in the demo.

### AnalogSearch on a single series will be noisy

Looking for "comparable magnitude spikes/dips" on a single FRED series will return a lot of false positives. Every CPI spike looks like every other CPI spike to a univariate matcher. Mitigations:

- Match on a small bundle of related series simultaneously (e.g. CPI plus breakevens plus a commodity proxy).
- Or: match the spike on the target series plus a rough macro regime tag (rates regime, growth regime) at the time of the spike.
- MacroComparator downstream is the safety net, but it should not be doing all the filtering work.

V1 of AnalogSearch can be single series. Plan to layer multi series matching once the pipeline runs end to end.

### Sensitivity scores are agent graded, not statistical

The rubric in the "Sensitivity and confidence" section gives bands, but the floor is still LLM judgment. Two mitigations:

- Require evidence citations for any score above 0.3.
- Run AdversaryAgent against every edge and store the transcript. The audit trail is the value, not the score itself.

If a mentor pushes on "is 0.6 actually 0.6", the answer is: it's a calibrated agent score with cited evidence and an adversarial check. We do not claim it's a regression coefficient.

### Decide on the stretch layer Friday morning, not Saturday

The news to tail scenario to feedback loop is the most ambitious piece. If we're going to attempt it, ScenarioAgent should be scaffolded Friday afternoon while context is fresh. If we wait until Saturday morning, we'll either skip it or rush a broken version into the demo. **Decide at Friday's design check in: in or out. No Schrödinger stretch.**

### Scope cut order if we're behind

Lowest cost first. Cut from the top of this list, not the bottom.

1. Stretch tail scenario layer (ScenarioAgent + news pull).
2. LogicVerifier (debate without formal verification still ships).
3. Second TreeBuilder pass (subtree expansion beyond first import).
4. Multi dimensional MacroComparator (collapse to 3 dimensions if needed).

**Do not cut adversarial debate.** That is the differentiated story for the demo.

## Setup quickstart

See `setup.md` for the full version. TL;DR:

```bash
# env
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# secrets
cp .env.example .env
# fill ANTHROPIC_API_KEY, FRED_API_KEY, HF_TOKEN, optionally PERPLEXITY_API_KEY

# HF login (datasets need it)
huggingface-cli login

# smoke test
python -m src.orchestrator --event "25% tariff on Chinese semiconductors" --dry-run
```

## Notes for Claude Code specifically

- **Stay in your assigned module.** See "Module ownership and parallel work" above. Multiple Claude Code sessions are running in parallel on this repo. If you edit outside your scope, you will conflict with a teammate. If you need a change elsewhere, leave a `# TODO(integration):` comment and stop.
- **Do not edit `src/types.py` unless you are the integrator.** If a type doesn't fit your needs, post in Slack and the integrator adds the field. Append only.
- When asked to add an agent, scaffold prompt + agent module + test in one go. Do not write the agent without a prompt file.
- Prefer editing existing tool wrappers over writing new ones. We will hit the same FRED series many times across agents, dedup at the tool layer.
- If a tool call fails, surface the error in the node/edge as `confidence = 0` with a note. Do not invent data.
- Keep diffs small. We'll be reviewing at speed.
- Run `pytest tests/test_<your_module>.py -x` before declaring anything done. Run the full suite only if you're the integrator merging.
- Read `prompts/<your_agent>.md` before editing the agent. The prompt is the spec, the Python is the wiring.