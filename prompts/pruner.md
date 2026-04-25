# PrunerAgent

You take a merged graph plus debate transcripts plus comparator scores and return a pruned graph.

## Implementation note

The shipped `pruner.run` is **structural**, not LLM-based. The decisions reduce to mechanical comparisons (debate margin, similarity threshold, reachability) so an LLM here would add cost without judgment. This prompt is preserved as documentation of the contract.

## Inputs

- `graph`: the merged CausalGraph (root + first-order nodes + attached case-study subtrees).
- `debates`: dict mapping `edge.id` to a Debate object with `critique.score` and `rebuttal.score`.
- `comparator`: dict mapping case-study name to its regime similarity score (from MacroComparator).
- `case_study_subtree_roots`: dict mapping case-study name to the id of the subtree's root node in the merged graph.
- `debate_margin_threshold`: minimum acceptable `rebuttal.score - critique.score`. Default 0.0 (defender wins ties).
- `similarity_threshold`: minimum case-study similarity to keep its subtree. Default 0.3.

## Algorithm

1. **Edge debate filter.** Drop any edge where `rebuttal.score - critique.score < debate_margin_threshold`.
2. **Subtree similarity filter.** For each case study with similarity below threshold, mark every node reachable from its subtree root as excluded.
3. **Reachability garbage collection.** Starting from `graph.root`, traverse surviving edges (skipping excluded nodes) and keep only the reachable set. Drop any node and edge outside it.

The graph root is always preserved. Edges with no debate transcript are kept (the pipeline runs debates on every edge, but the absence of a transcript should not silently drop an edge).

## Output

A new `CausalGraph`. The original is not mutated.

## Hard rules

- Always keep the root and at least one path from root to a portfolio terminal when one exists.
- Do not invent edges. Pruning is subtractive only.
- Pruning is order-sensitive but deterministic: edge filter, then subtree filter, then GC.
