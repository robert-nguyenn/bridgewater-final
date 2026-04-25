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


def _new_consensus_node_id() -> str:
    return f"cn_{uuid.uuid4().hex[:8]}"


def _new_consensus_edge_id() -> str:
    return f"ce_{uuid.uuid4().hex[:8]}"


@dataclass
class ConsensusNode:
    """An archetype node clustered across surviving case-study subtrees.

    `member_node_ids` is the list of (case_study_id, original_node_id) pairs
    that contributed to this cluster — drives the "appears in: X, Y, Z"
    attribution list in the drawer. `consensus_weight` is the share of
    surviving case studies that voted for this archetype, in [0, 1].

    Trunk nodes (root + first-order) are represented as singleton clusters
    with member_node_ids=[("trunk", original_id)] so the trunk preserves its
    identity through this stage.
    """
    id: str
    label: str
    description: str
    layer: int
    asset_class: Optional[str] = None
    member_node_ids: list[tuple[str, str]] = field(default_factory=list)
    member_count: int = 0  # |distinct contributing case studies|
    consensus_weight: float = 0.0  # in [0, 1]


@dataclass
class ConsensusEdge:
    """A pooled directed edge in the consensus graph.

    All original edges sharing the same (src_cluster, dst_cluster) collapse
    here. `member_edge_ids` carries the (case_study_id, original_edge_id)
    attribution. `consensus_weight` is the share of surviving case studies
    that contributed at least one original edge. `avg_sensitivity` /
    `avg_confidence` are means over the original edges (single-analog
    conviction), distinct from consensus_weight (cross-analog agreement)."""
    src: str  # ConsensusNode.id
    dst: str  # ConsensusNode.id
    canonical_mechanism: str
    avg_sensitivity: float
    avg_confidence: float
    consensus_weight: float = 0.0  # in [0, 1]
    member_count: int = 0
    member_edge_ids: list[tuple[str, str]] = field(default_factory=list)
    member_mechanisms: list[str] = field(default_factory=list)
    id: str = field(default_factory=_new_consensus_edge_id)


@dataclass
class ConsensusGraph:
    nodes: dict[str, ConsensusNode] = field(default_factory=dict)
    edges: list[ConsensusEdge] = field(default_factory=list)
    root: Optional[str] = None
    case_study_index: dict[str, str] = field(default_factory=dict)
    # cs_id -> human-readable name, used by the UI drawer for attribution.
