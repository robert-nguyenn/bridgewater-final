import { Building2, FileText, LineChart, MessageSquareQuote, Newspaper } from "lucide-react";
import type { Evidence } from "../../types";

const ICONS = {
  fred_series: LineChart,
  ticker: Building2,
  fundamentals: FileText,
  speech: MessageSquareQuote,
  article: Newspaper,
} as const;

export function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  if (evidence.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-line p-3 text-[11.5px] text-ink-dim">
        No evidence cited.
      </div>
    );
  }
  return (
    <ul className="flex flex-col gap-1.5">
      {evidence.map((e, i) => {
        const Icon = ICONS[e.kind] ?? FileText;
        return (
          <li
            key={`${e.kind}:${e.ref}:${i}`}
            className="flex items-start gap-2 rounded-md border border-line bg-bg-elev px-2.5 py-2"
          >
            <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-[11.5px] text-ink">{e.ref}</span>
                <span className="shrink-0 text-[9px] uppercase tracking-wider text-ink-dim">
                  {e.kind.replace("_", " ")}
                </span>
              </div>
              {e.note && <p className="mt-0.5 text-[11px] text-ink-mute">{e.note}</p>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
