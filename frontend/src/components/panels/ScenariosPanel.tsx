import { Compass } from "lucide-react";
import type { TailScenario } from "../../types";
import { ScoreBar } from "../ui/ScoreBar";

export function ScenariosPanel({
  scenarios,
  onPickScenario,
}: {
  scenarios: TailScenario[];
  onPickScenario?: (text: string) => void;
}) {
  return (
    <div className="p-3">
      {scenarios.length === 0 ? (
        <div className="rounded-md border border-dashed border-line p-3 text-[11.5px] text-ink-dim">
          No tail scenarios yet.
        </div>
      ) : (
        <ul className="flex flex-col gap-2">
          {scenarios.map((s) => (
            <li key={s.id} className="rounded-lg border border-line bg-bg-elev p-2.5">
              <div className="flex items-start gap-2">
                <Compass className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-[12.5px] font-medium text-ink">{s.headline}</p>
                    <span className="font-mono text-[10.5px] text-ink-mute">
                      p={s.probability.toFixed(2)}
                    </span>
                  </div>
                  <p className="mt-0.5 text-[10.5px] text-ink-dim">{s.source}</p>
                  <p className="mt-1 text-[11.5px] text-ink-mute">{s.policy_event}</p>
                  <div className="mt-2 flex items-center gap-2">
                    <div className="flex-1">
                      <ScoreBar value={s.probability} showValue={false} />
                    </div>
                    {onPickScenario && (
                      <button
                        type="button"
                        onClick={() => onPickScenario(s.policy_event)}
                        className="chip"
                      >
                        Use as event
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
