from __future__ import annotations

import argparse
import sys
from typing import Optional

from src.config import MODEL
from src.types import CausalGraph, ToolBundle


def run_pipeline(event: str, *, dry_run: bool = False, model: str = MODEL) -> CausalGraph:
    """Stage 1 to 10. See CLAUDE.md 'Pipeline (end to end)'."""
    tools = ToolBundle()
    graph = CausalGraph()
    # TODO(integration): stage 1 IdeaAgent
    # TODO(integration): stage 2 AnalogSearchAgent
    # TODO(integration): stage 3 TreeBuilder + Sensitivity
    # TODO(integration): stage 4 LogicVerifier
    # TODO(integration): stage 5 Adversary vs Defender
    # TODO(integration): stage 6 MacroComparator
    # TODO(integration): stage 7 merge case study subtrees
    # TODO(integration): stage 8 second TreeBuilder pass
    # TODO(integration): stage 9 PortfolioAgent
    # TODO(integration): stage 10 ScenarioAgent (stretch)
    if dry_run:
        print(f"[dry-run] event={event!r} model={model}")
    return graph


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="policy-mapper")
    parser.add_argument("--event", required=True, help="plain English policy event")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args(argv)
    run_pipeline(args.event, dry_run=args.dry_run, model=args.model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
