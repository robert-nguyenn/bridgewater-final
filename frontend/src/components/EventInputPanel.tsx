import { Play, RotateCcw, Square, Wand2 } from "lucide-react";
import { useMemo } from "react";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Card } from "./ui/Card";

const EXAMPLES: { label: string; text: string }[] = [
  {
    label: "Semi tariff",
    text: "25% tariff on Chinese semiconductors",
  },
  {
    label: "ECB lending",
    text: "ECB launches emergency lending facility for southern European banks",
  },
  {
    label: "OPEC cut",
    text: "OPEC+ announces 1.5 mbpd surprise production cut effective next month",
  },
  {
    label: "Fed pivot",
    text: "Fed signals 75bp of cuts over next two meetings on labor weakness",
  },
];

const MODELS = [
  { id: "claude-opus-4-7", label: "Opus 4.7 (heavy)" },
  { id: "claude-sonnet-4-6", label: "Sonnet 4.6 (fast)" },
];

interface Props {
  status: "idle" | "running" | "done" | "error";
  event: string;
  model: string;
  onEventChange: (s: string) => void;
  onModelChange: (s: string) => void;
  onRun: (event: string, model: string) => void;
  onCancel: () => void;
  onReset: () => void;
}

export function EventInputPanel({
  status,
  event,
  model,
  onEventChange,
  onModelChange,
  onRun,
  onCancel,
  onReset,
}: Props) {
  const validation = useMemo(() => validate(event), [event]);
  const isRunning = status === "running";
  const canRun = !isRunning && validation.ok;

  return (
    <Card
      title="Policy event"
      subtitle="Plain-English description of a rare or out-of-sample policy action."
      right={
        <Badge tone={validation.ok ? "ok" : "warn"} dot>
          {validation.ok ? "ready" : validation.message}
        </Badge>
      }
      bodyClassName="flex min-h-0 flex-col gap-3 p-4"
    >
      <textarea
        value={event}
        onChange={(e) => onEventChange(e.target.value)}
        spellCheck={false}
        placeholder="e.g. ECB launches emergency lending facility for southern European banks"
        rows={4}
        disabled={isRunning}
        className="w-full resize-none rounded-md border border-line bg-bg-base px-3 py-2.5 text-sm text-ink placeholder:text-ink-dim focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/30 disabled:opacity-60"
      />

      <div>
        <div className="label-mini mb-1.5">Examples</div>
        <div className="flex flex-wrap gap-1.5">
          {EXAMPLES.map((ex) => (
            <button
              key={ex.label}
              type="button"
              onClick={() => onEventChange(ex.text)}
              disabled={isRunning}
              className="chip disabled:opacity-50"
            >
              <Wand2 className="h-3 w-3" />
              {ex.label}
            </button>
          ))}
        </div>
      </div>

      <div>
        <div className="label-mini mb-1.5">Model</div>
        <div className="flex gap-1.5">
          {MODELS.map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() => onModelChange(m.id)}
              disabled={isRunning}
              className={`chip ${
                model === m.id
                  ? "border-accent/40 bg-accent/10 text-accent hover:text-accent"
                  : ""
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-auto flex items-center gap-2">
        {!isRunning ? (
          <Button
            variant="primary"
            size="md"
            disabled={!canRun}
            onClick={() => onRun(event.trim(), model)}
            leadingIcon={<Play className="h-3.5 w-3.5" />}
            className="flex-1"
          >
            Map impact
          </Button>
        ) : (
          <Button
            variant="danger"
            size="md"
            onClick={onCancel}
            leadingIcon={<Square className="h-3.5 w-3.5" />}
            className="flex-1"
          >
            Cancel run
          </Button>
        )}
        <Button
          variant="ghost"
          size="md"
          onClick={onReset}
          disabled={isRunning}
          leadingIcon={<RotateCcw className="h-3.5 w-3.5" />}
        >
          Reset
        </Button>
      </div>
    </Card>
  );
}

function validate(event: string): { ok: boolean; message: string } {
  const trimmed = event.trim();
  if (!trimmed) return { ok: false, message: "event required" };
  if (trimmed.length < 12) return { ok: false, message: "describe more" };
  if (trimmed.length > 600) return { ok: false, message: "too long" };
  return { ok: true, message: "ready" };
}
