import { ArrowDownRight, ArrowRightLeft, ArrowUpRight } from "lucide-react";
import { num, pct } from "../../lib/format";
import type { CaseStudy, MacroSnapshot } from "../../types";
import { Badge } from "../ui/Badge";
import { ScoreBar } from "../ui/ScoreBar";

interface Props {
  macroNow?: MacroSnapshot;
  caseStudies: CaseStudy[];
}

const FIELDS: { key: keyof MacroSnapshot; label: string; pct: boolean; digits?: number }[] = [
  { key: "cpi_yoy", label: "CPI yoy", pct: true },
  { key: "core_pce_yoy", label: "Core PCE yoy", pct: true },
  { key: "fed_funds", label: "Fed funds", pct: true },
  { key: "ten_year", label: "10y UST", pct: true },
  { key: "dxy", label: "DXY", pct: false, digits: 1 },
  { key: "unemployment", label: "Unemployment", pct: true },
  { key: "real_gdp_yoy", label: "Real GDP yoy", pct: true },
];

export function MacroComparePanel({ macroNow, caseStudies }: Props) {
  return (
    <div className="grid grid-cols-1 gap-0 lg:grid-cols-[260px_1fr]">
        {/* Today snapshot */}
        <div className="border-b border-line p-3 lg:border-b-0 lg:border-r">
          <div className="label-mini mb-2">Today</div>
          {!macroNow ? (
            <Empty hint="Awaiting MacroComparator stage." />
          ) : (
            <ul className="flex flex-col gap-1">
              {FIELDS.map((f) => {
                const v = macroNow[f.key];
                return (
                  <li
                    key={f.key}
                    className="flex items-center justify-between rounded-md px-1.5 py-1 text-[11.5px]"
                  >
                    <span className="text-ink-mute">{f.label}</span>
                    <span className="font-mono text-ink">
                      {f.pct ? pct(v, 1) : num(v, f.digits ?? 2)}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* Case studies */}
        <div className="p-3">
          <div className="mb-2 flex items-center justify-between">
            <div className="label-mini">Analog case studies</div>
            <span className="font-mono text-[10px] text-ink-dim">
              {caseStudies.length} found
            </span>
          </div>
          {caseStudies.length === 0 ? (
            <Empty hint="AnalogSearch has not produced case studies yet." />
          ) : (
            <ul className="flex flex-col gap-2">
              {caseStudies.map((cs) => (
                <li key={cs.id} className="rounded-lg border border-line bg-bg-elev p-2.5">
                  <div className="flex flex-wrap items-center justify-between gap-1.5">
                    <div className="min-w-0">
                      <p className="text-[12.5px] font-medium text-ink">{cs.name}</p>
                      <p className="text-[10.5px] text-ink-dim">
                        {cs.date_range[0]} → {cs.date_range[1]} · attaches to {cs.attaches_to}
                      </p>
                    </div>
                    <Badge tone={similarityTone(cs.similarity_score)}>
                      similarity {Math.round(cs.similarity_score * 100)}%
                    </Badge>
                  </div>
                  <div className="mt-2">
                    <ScoreBar value={cs.similarity_score} showValue={false} />
                  </div>
                  <p className="mt-1.5 line-clamp-2 text-[11px] text-ink-mute">{cs.triggering_event}</p>
                  {macroNow && (
                    <div className="mt-2 grid grid-cols-3 gap-1.5 sm:grid-cols-4 lg:grid-cols-7">
                      {FIELDS.map((f) => (
                        <DiffCell
                          key={f.key}
                          label={f.label}
                          now={macroNow[f.key]}
                          then={cs.macro_snapshot[f.key]}
                          asPct={f.pct}
                          digits={f.digits ?? 2}
                        />
                      ))}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
    </div>
  );
}

function similarityTone(s: number) {
  if (s >= 0.7) return "ok" as const;
  if (s >= 0.5) return "accent" as const;
  if (s >= 0.3) return "warn" as const;
  return "bad" as const;
}

function DiffCell({
  label,
  now,
  then,
  asPct,
  digits,
}: {
  label: string;
  now?: number | null;
  then?: number | null;
  asPct: boolean;
  digits: number;
}) {
  if (now === null || now === undefined || then === null || then === undefined) {
    return (
      <div className="rounded-md border border-line bg-bg-base px-1.5 py-1">
        <div className="text-[9px] text-ink-dim">{label}</div>
        <div className="font-mono text-[11px] text-ink-dim">—</div>
      </div>
    );
  }
  const diff = now - then;
  const Icon = diff > 0.0001 ? ArrowUpRight : diff < -0.0001 ? ArrowDownRight : ArrowRightLeft;
  const tone =
    Math.abs(diff) < (asPct ? 0.005 : 1) ? "text-ink-mute" : diff > 0 ? "text-warn" : "text-accent";
  return (
    <div className="rounded-md border border-line bg-bg-base px-1.5 py-1">
      <div className="text-[9px] text-ink-dim">{label}</div>
      <div className="flex items-center justify-between font-mono text-[11px]">
        <span className="text-ink-mute">{asPct ? pct(then, 1) : num(then, digits)}</span>
        <Icon className={`h-2.5 w-2.5 ${tone}`} />
        <span className="text-ink">{asPct ? pct(now, 1) : num(now, digits)}</span>
      </div>
    </div>
  );
}

function Empty({ hint }: { hint: string }) {
  return (
    <div className="rounded-md border border-dashed border-line p-3 text-[11.5px] text-ink-dim">
      {hint}
    </div>
  );
}
