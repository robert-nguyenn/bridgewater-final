import { ASSET_COLORS, ASSET_LABELS, pct } from "../../lib/format";
import type { CaseStudy, Node } from "../../types";
import { Badge } from "../ui/Badge";
import { EvidenceList } from "./EvidenceList";

export function NodeInspector({
  node,
  attachedAnalogs,
}: {
  node: Node;
  attachedAnalogs: CaseStudy[];
}) {
  const color = node.asset_class ? ASSET_COLORS[node.asset_class] : "#5eb1ff";
  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="mb-1 flex items-center gap-2">
          <Badge tone="muted">L{node.layer}</Badge>
          {node.asset_class && (
            <span
              className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px]"
              style={{
                borderColor: `${color}55`,
                background: `${color}1a`,
                color,
              }}
            >
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
              {ASSET_LABELS[node.asset_class]}
            </span>
          )}
          {node.magnitude_estimate !== null && node.magnitude_estimate !== undefined && (
            <Badge tone={node.magnitude_estimate >= 0 ? "ok" : "bad"}>
              magnitude {pct(node.magnitude_estimate)}
            </Badge>
          )}
        </div>
        <h3 className="text-base font-semibold text-ink">{node.label}</h3>
        <p className="mt-1 text-[12.5px] leading-snug text-ink-mute">{node.description}</p>
      </div>

      <Section title="Evidence" count={node.evidence.length}>
        <EvidenceList evidence={node.evidence} />
      </Section>

      <Section title="Attached analogs" count={attachedAnalogs.length}>
        {attachedAnalogs.length === 0 ? (
          <Empty hint="No historical analog has been attached to this node yet." />
        ) : (
          <ul className="flex flex-col gap-2">
            {attachedAnalogs.map((cs) => (
              <li key={cs.id} className="rounded-lg border border-line bg-bg-elev p-2.5">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-[12.5px] font-medium text-ink">{cs.name}</p>
                  <Badge tone={similarityTone(cs.similarity_score)}>
                    sim {Math.round(cs.similarity_score * 100)}%
                  </Badge>
                </div>
                <p className="mt-0.5 text-[10.5px] text-ink-dim">
                  {cs.date_range[0]} → {cs.date_range[1]}
                </p>
                <p className="mt-1 line-clamp-2 text-[11.5px] text-ink-mute">{cs.triggering_event}</p>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <h4 className="label-mini">{title}</h4>
        {count !== undefined && (
          <span className="text-[10px] font-mono text-ink-dim">{count}</span>
        )}
      </div>
      {children}
    </section>
  );
}

function Empty({ hint }: { hint: string }) {
  return (
    <div className="rounded-md border border-dashed border-line p-3 text-[11.5px] text-ink-dim">
      {hint}
    </div>
  );
}

function similarityTone(s: number) {
  if (s >= 0.7) return "ok" as const;
  if (s >= 0.5) return "accent" as const;
  if (s >= 0.3) return "warn" as const;
  return "bad" as const;
}
