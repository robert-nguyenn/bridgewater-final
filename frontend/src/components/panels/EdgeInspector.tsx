import { ArrowRight, ShieldQuestion, Swords } from "lucide-react";
import { bandLabel } from "../../lib/format";
import type { Debate, Edge, Node } from "../../types";
import { Badge } from "../ui/Badge";
import { ScoreBar } from "../ui/ScoreBar";
import { EvidenceList } from "./EvidenceList";

export function EdgeInspector({
  edge,
  src,
  dst,
  debate,
  pruned,
}: {
  edge: Edge;
  src?: Node;
  dst?: Node;
  debate?: Debate;
  pruned?: boolean;
}) {
  return (
    <div className="flex flex-col gap-4">
      {/* Endpoints */}
      <div className="rounded-lg border border-line bg-bg-elev p-2.5">
        <div className="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-ink-dim">
          <span>Edge</span>
          {pruned && <Badge tone="bad">pruned</Badge>}
        </div>
        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 text-[12px]">
          <span className="truncate font-medium text-ink" title={src?.label}>
            {src?.label ?? edge.src}
          </span>
          <ArrowRight className="h-3.5 w-3.5 text-ink-mute" />
          <span className="truncate text-right font-medium text-ink" title={dst?.label}>
            {dst?.label ?? edge.dst}
          </span>
        </div>
      </div>

      {/* Mechanism */}
      <section>
        <h4 className="label-mini mb-1.5">Mechanism</h4>
        <p className="text-[12.5px] leading-snug text-ink">{edge.mechanism}</p>
      </section>

      {/* Scores */}
      <section className="space-y-3">
        <ScoreBar
          label={`Sensitivity · ${bandLabel(edge.sensitivity)}`}
          value={edge.sensitivity}
        />
        <ScoreBar
          label={`Confidence · ${bandLabel(edge.confidence)}`}
          value={edge.confidence}
        />
      </section>

      {/* Debate */}
      {debate && (
        <section>
          <div className="mb-2 flex items-center justify-between">
            <h4 className="label-mini">Adversarial debate</h4>
            <Badge tone={debate.survives ? "ok" : "bad"}>
              {debate.survives ? "survives" : "flagged"} · margin {(debate.margin ?? 0).toFixed(2)}
            </Badge>
          </div>
          <div className="space-y-2">
            <DebateBlock
              icon={<Swords className="h-3.5 w-3.5" />}
              tone="bad"
              role="Adversary"
              score={debate.critique.score}
              text={debate.critique.argument}
              cites={debate.critique.citations}
            />
            <DebateBlock
              icon={<ShieldQuestion className="h-3.5 w-3.5" />}
              tone="ok"
              role="Defender"
              score={debate.rebuttal.score}
              text={debate.rebuttal.argument}
              cites={debate.rebuttal.citations}
            />
          </div>
        </section>
      )}

      {/* Supporting data */}
      <section>
        <h4 className="label-mini mb-2">Supporting data</h4>
        <EvidenceList evidence={edge.supporting_data} />
      </section>
    </div>
  );
}

function DebateBlock({
  icon,
  tone,
  role,
  score,
  text,
  cites,
}: {
  icon: React.ReactNode;
  tone: "ok" | "bad";
  role: string;
  score: number;
  text: string;
  cites: string[];
}) {
  return (
    <div
      className={`rounded-lg border p-2.5 ${
        tone === "ok" ? "border-ok/25 bg-ok/5" : "border-bad/25 bg-bad/5"
      }`}
    >
      <div className="mb-1 flex items-center justify-between">
        <span className={`flex items-center gap-1.5 text-[11px] font-medium ${tone === "ok" ? "text-ok" : "text-bad"}`}>
          {icon}
          {role}
        </span>
        <span className="font-mono text-[10px] text-ink-mute">score {score.toFixed(2)}</span>
      </div>
      <p className="text-[11.5px] leading-snug text-ink">{text}</p>
      {cites.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {cites.map((c, i) => (
            <span key={i} className="font-mono text-[10px] text-ink-dim">· {c}</span>
          ))}
        </div>
      )}
    </div>
  );
}
