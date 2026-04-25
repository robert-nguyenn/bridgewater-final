from __future__ import annotations

from src.orchestrator import run_pipeline
from src.types import CausalGraph


def test_orchestrator_dry_run():
    g = run_pipeline("test event", dry_run=True)
    assert isinstance(g, CausalGraph)
