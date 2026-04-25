import { Trash2 } from "lucide-react";
import { useEffect, useRef } from "react";
import { timeOfDay } from "../lib/format";
import type { LogEntry } from "../types";
import { Button } from "./ui/Button";
import { Card } from "./ui/Card";

export function LogStream({
  entries,
  onClear,
}: {
  entries: LogEntry[];
  onClear?: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // auto-scroll if user is near the bottom
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (nearBottom) {
      el.scrollTop = el.scrollHeight;
    }
  }, [entries.length]);

  return (
    <Card
      title="Run log"
      subtitle={`${entries.length} entr${entries.length === 1 ? "y" : "ies"}`}
      right={
        onClear && entries.length > 0 ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={onClear}
            leadingIcon={<Trash2 className="h-3 w-3" />}
          >
            Clear
          </Button>
        ) : null
      }
      bodyClassName="p-0"
    >
      <div
        ref={ref}
        className="max-h-[180px] min-h-[120px] overflow-y-auto bg-bg-base px-3 py-2 font-mono text-[11px] leading-relaxed"
      >
        {entries.length === 0 ? (
          <div className="text-ink-dim">No log output yet. Run a scenario to see live agent activity.</div>
        ) : (
          entries.map((e, i) => (
            <div key={i} className="flex items-start gap-2">
              <span className="shrink-0 text-ink-dim">{timeOfDay(e.ts)}</span>
              <span className={`${toneFor(e.level)} w-10 uppercase tracking-wider text-[10px]`}>
                {e.level}
              </span>
              {e.stage !== undefined && (
                <span className="shrink-0 text-ink-dim">s{e.stage}</span>
              )}
              {e.agent && <span className="shrink-0 text-accent">{e.agent}</span>}
              <span className="text-ink">{e.message}</span>
            </div>
          ))
        )}
      </div>
    </Card>
  );
}

function toneFor(level: LogEntry["level"]) {
  switch (level) {
    case "warn":
      return "shrink-0 text-warn";
    case "error":
      return "shrink-0 text-bad";
    case "debug":
      return "shrink-0 text-ink-dim";
    default:
      return "shrink-0 text-ink-mute";
  }
}
