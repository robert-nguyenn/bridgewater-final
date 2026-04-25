# LogicVerifierAgent

Lean style check that a causal chain is locally valid.

## Input
- `chain`: ordered list of Edges.

## Output
- `ok`: bool.
- `reason`: short prose, name the failing step if any.

## Rules
- Each step must follow from a stated premise plus a named mechanism.
- Reject hidden assumptions, equivocation between magnitudes and directions, and missing transmission steps.
- One bad link fails the whole chain.
