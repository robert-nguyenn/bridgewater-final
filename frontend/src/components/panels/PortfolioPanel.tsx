import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { ASSET_COLORS, ASSET_LABELS } from "../../lib/format";
import type { PortfolioImpact } from "../../types";
import { Badge } from "../ui/Badge";

export function PortfolioPanel({ impacts }: { impacts: PortfolioImpact[] }) {
  return (
    <div className="p-3">
      {impacts.length === 0 ? (
        <div className="rounded-md border border-dashed border-line p-3 text-[11.5px] text-ink-dim">
          Awaiting PortfolioAgent (stage 9).
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {impacts.map((p) => (
            <div key={p.asset_class} className="rounded-lg border border-line bg-bg-elev p-2.5">
              <div className="mb-2 flex items-center justify-between">
                <span
                  className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px]"
                  style={{
                    borderColor: `${ASSET_COLORS[p.asset_class]}55`,
                    background: `${ASSET_COLORS[p.asset_class]}1a`,
                    color: ASSET_COLORS[p.asset_class],
                  }}
                >
                  <span
                    className="h-1.5 w-1.5 rounded-full"
                    style={{ background: ASSET_COLORS[p.asset_class] }}
                  />
                  {ASSET_LABELS[p.asset_class]}
                </span>
                <span className="font-mono text-[10px] text-ink-dim">
                  {p.instruments.length} idea{p.instruments.length === 1 ? "" : "s"}
                </span>
              </div>
              <ul className="flex flex-col gap-1.5">
                {p.instruments.map((ins) => {
                  const Icon =
                    ins.direction === "long"
                      ? ArrowUpRight
                      : ins.direction === "short"
                      ? ArrowDownRight
                      : Minus;
                  const tone =
                    ins.direction === "long"
                      ? "ok"
                      : ins.direction === "short"
                      ? "bad"
                      : "neutral";
                  return (
                    <li
                      key={ins.ticker}
                      className="rounded-md border border-line bg-bg-base px-2 py-1.5"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5">
                            <Icon
                              className={`h-3 w-3 ${
                                tone === "ok" ? "text-ok" : tone === "bad" ? "text-bad" : "text-ink-mute"
                              }`}
                            />
                            <span className="font-mono text-[12px] text-ink">{ins.ticker}</span>
                            <Badge tone={tone as "ok" | "bad" | "neutral"}>
                              {ins.direction}
                            </Badge>
                          </div>
                          <p className="truncate text-[10.5px] text-ink-mute">{ins.name}</p>
                        </div>
                        {ins.expected_move_bps !== undefined && (
                          <span
                            className={`shrink-0 font-mono text-[11px] ${
                              ins.expected_move_bps > 0 ? "text-ok" : "text-bad"
                            }`}
                          >
                            {ins.expected_move_bps > 0 ? "+" : ""}
                            {ins.expected_move_bps} bps
                          </span>
                        )}
                      </div>
                      <p className="mt-1 text-[11px] leading-snug text-ink-mute">{ins.rationale}</p>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
