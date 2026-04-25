// Mirrors src/types.py. Append-only on the Python side; same here.

export type AssetClass =
  | "equities"
  | "futures"
  | "commodities"
  | "fx"
  | "rates"
  | "macro";

export type EvidenceKind =
  | "fred_series"
  | "ticker"
  | "speech"
  | "article"
  | "fundamentals";

export interface Evidence {
  kind: EvidenceKind;
  ref: string;
  note?: string | null;
  payload?: Record<string, unknown> | null;
}

export interface Node {
  id: string;
  label: string;
  description: string;
  layer: number;
  asset_class?: AssetClass | null;
  magnitude_estimate?: number | null;
  evidence: Evidence[];
}

export interface Edge {
  id: string;
  src: string;
  dst: string;
  mechanism: string;
  sensitivity: number; // 0..1
  confidence: number; // 0..1
  supporting_data: Evidence[];
  adversary_notes?: string | null;
}

export interface MacroSnapshot {
  cpi_yoy?: number | null;
  core_pce_yoy?: number | null;
  fed_funds?: number | null;
  ten_year?: number | null;
  dxy?: number | null;
  unemployment?: number | null;
  real_gdp_yoy?: number | null;
}

export interface CausalGraph {
  nodes: Record<string, Node>;
  edges: Edge[];
  root: string | null;
}

export interface CaseStudy {
  id: string;
  name: string;
  date_range: [string, string];
  triggering_event: string;
  macro_snapshot: MacroSnapshot;
  similarity_score: number; // 0..1 vs today
  // node id this analog is attached to
  attaches_to: string;
  subtree: CausalGraph;
}

export interface Episode {
  series_id: string;
  start: string;
  end: string;
  magnitude: number;
  candidate_event?: string | null;
}

// Adversarial debate
export interface Critique {
  score: number; // adversary strength
  argument: string;
  citations: string[];
}

export interface Rebuttal {
  score: number; // defender strength
  argument: string;
  citations: string[];
}

export interface Debate {
  target_id: string; // edge id (or node id when run on nodes)
  critique: Critique;
  rebuttal: Rebuttal;
  // derived
  margin?: number;
  survives?: boolean;
}

// Portfolio impact
export interface PortfolioImpact {
  asset_class: AssetClass;
  instruments: {
    ticker: string;
    name: string;
    direction: "long" | "short" | "mixed";
    expected_move_bps?: number;
    rationale: string;
  }[];
}

// Tail scenarios (stretch)
export interface TailScenario {
  id: string;
  headline: string;
  probability: number; // 0..1
  source: string;
  policy_event: string;
}

// Pipeline stage descriptor
export type StageStatus = "pending" | "active" | "done" | "skipped" | "error";

export interface Stage {
  id: number;
  key: string;
  label: string;
  description: string;
  status: StageStatus;
  // optional metric to display once done
  metric?: string;
}

// Live log entry
export interface LogEntry {
  ts: number;
  stage?: number;
  agent?: string;
  level: "info" | "warn" | "error" | "debug";
  message: string;
}

// Convenience aggregate that the simulator builds up
export interface RunState {
  status: "idle" | "running" | "done" | "error";
  event: string;
  model: string;
  startedAt?: number;
  finishedAt?: number;
  graph: CausalGraph;
  stages: Stage[];
  caseStudies: CaseStudy[];
  debates: Record<string, Debate>;
  macroNow?: MacroSnapshot;
  portfolio: PortfolioImpact[];
  scenarios: TailScenario[];
  log: LogEntry[];
}
