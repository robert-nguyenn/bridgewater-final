from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

# Skeleton types. Integrator owns this file. Append-only after merge.
# See CLAUDE.md "Key abstractions" for the contract.


def _new_edge_id() -> str:
    return f"e_{uuid.uuid4().hex[:8]}"


@dataclass
class Evidence:
    kind: str  # "fred_series" | "ticker" | "speech" | "article" | "fundamentals"
    ref: str  # series id, ticker, doc id, url
    note: Optional[str] = None
    payload: Optional[dict[str, Any]] = None


@dataclass
class Node:
    id: str
    label: str
    description: str
    layer: int
    asset_class: Optional[str] = None
    magnitude_estimate: Optional[float] = None
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class Edge:
    src: str
    dst: str
    mechanism: str
    sensitivity: float
    confidence: float
    id: str = field(default_factory=_new_edge_id)
    supporting_data: list[Evidence] = field(default_factory=list)
    adversary_notes: Optional[str] = None


@dataclass
class MacroSnapshot:
    cpi_yoy: Optional[float] = None
    core_pce_yoy: Optional[float] = None
    fed_funds: Optional[float] = None
    ten_year: Optional[float] = None
    dxy: Optional[float] = None
    unemployment: Optional[float] = None
    real_gdp_yoy: Optional[float] = None


@dataclass
class CausalGraph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    root: Optional[str] = None


def _new_case_study_id() -> str:
    return f"cs_{uuid.uuid4().hex[:8]}"


@dataclass
class CaseStudy:
    name: str
    date_range: tuple[date, date]
    triggering_event: str
    macro_snapshot: MacroSnapshot
    similarity_score: float
    subtree: CausalGraph
    id: str = field(default_factory=_new_case_study_id)


@dataclass
class Episode:
    series_id: str
    start: date
    end: date
    magnitude: float
    candidate_event: Optional[str] = None


@dataclass
class ToolError:
    tool: str
    args: dict[str, Any]
    message: str


@dataclass
class ToolBundle:
    fred: Any = None
    yahoo: Any = None
    hf: Any = None
    central_bank: Any = None
    news: Any = None
