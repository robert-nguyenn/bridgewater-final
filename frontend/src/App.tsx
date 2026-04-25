import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";

import { Header } from "./components/Header";
import { EventInputPanel } from "./components/EventInputPanel";
import { PipelineStages } from "./components/PipelineStages";
import { GraphCanvas } from "./components/graph/GraphCanvas";
import { InspectorPane } from "./components/InspectorPane";
import { LogStream } from "./components/LogStream";
import { MacroComparePanel } from "./components/panels/MacroComparePanel";
import { PortfolioPanel } from "./components/panels/PortfolioPanel";
import { ScenariosPanel } from "./components/panels/ScenariosPanel";
import { Tabs } from "./components/ui/Tabs";
import {
  applyEvent,
  emptyPipelineState,
  runMockPipeline,
  type PipelineEvent,
  type PipelineState,
} from "./lib/pipeline";
import { freshStages } from "./lib/stages";

type Action =
  | { type: "event"; payload: PipelineEvent }
  | { type: "reset"; payload: PipelineState }
  | { type: "clear_log" };

function reducer(state: PipelineState, action: Action): PipelineState {
  switch (action.type) {
    case "event":
      return applyEvent(state, action.payload);
    case "reset":
      return action.payload;
    case "clear_log":
      return { ...state, log: [] };
  }
}

const DEFAULT_EVENT = "25% tariff on Chinese semiconductors";
const DEFAULT_MODEL = "claude-opus-4-7";

export default function App() {
  const [state, dispatch] = useReducer(reducer, undefined, () =>
    emptyPipelineState(freshStages())
  );

  const [event, setEvent] = useState(DEFAULT_EVENT);
  const [model, setModel] = useState(DEFAULT_MODEL);
  const [selection, setSelection] = useState<{ kind: "node" | "edge"; id: string } | null>(null);
  const [bottomTab, setBottomTab] = useState<"macro" | "portfolio" | "scenarios">("macro");

  const abortRef = useRef<AbortController | null>(null);
  const [flowingEdgeIds, setFlowingEdgeIds] = useState<Set<string>>(() => new Set());

  // Tick header timer while running so elapsed updates.
  const [, tick] = useState(0);
  useEffect(() => {
    if (state.status !== "running") return;
    const id = setInterval(() => tick((n) => n + 1), 250);
    return () => clearInterval(id);
  }, [state.status]);

  const onRun = useCallback(
    async (eventText: string, modelId: string) => {
      // Reset state
      dispatch({ type: "reset", payload: emptyPipelineState(freshStages()) });
      setSelection(null);
      setFlowingEdgeIds(new Set());
      setEvent(eventText);
      setModel(modelId);

      const ctrl = new AbortController();
      abortRef.current = ctrl;

      await runMockPipeline(
        { event: eventText, model: modelId, signal: ctrl.signal, speed: 1 },
        (evt) => {
          // Track which edges should briefly animate as "flowing".
          if (evt.type === "edge:add") {
            const newId = evt.edge.id;
            setFlowingEdgeIds((prev) => {
              const next = new Set(prev);
              next.add(newId);
              return next;
            });
            window.setTimeout(() => {
              setFlowingEdgeIds((prev) => {
                if (!prev.has(newId)) return prev;
                const next = new Set(prev);
                next.delete(newId);
                return next;
              });
            }, 1400);
          }
          dispatch({ type: "event", payload: evt });
        }
      );
    },
    []
  );

  const onCancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const onReset = useCallback(() => {
    abortRef.current?.abort();
    dispatch({ type: "reset", payload: emptyPipelineState(freshStages()) });
    setSelection(null);
    setFlowingEdgeIds(new Set());
  }, []);

  // When something gets pruned, clear the selection if it referred to it.
  useEffect(() => {
    if (!selection) return;
    if (selection.kind === "edge" && state.prunedEdgeIds.has(selection.id)) {
      // keep selected so user can see why; do nothing
    }
    if (selection.kind === "node" && state.prunedNodeIds.has(selection.id)) {
      setSelection(null);
    }
  }, [selection, state.prunedEdgeIds, state.prunedNodeIds]);

  const bottomTabs = useMemo(
    () => [
      { key: "macro", label: "Macro then vs now", count: state.caseStudies.length },
      { key: "portfolio", label: "Portfolio impact", count: state.portfolio.length },
      { key: "scenarios", label: "Tail scenarios", count: state.scenarios.length },
    ],
    [state.caseStudies.length, state.portfolio.length, state.scenarios.length]
  );

  return (
    <div className="flex h-full min-h-screen flex-col bg-bg-base text-ink">
      <Header
        status={state.status}
        model={model}
        nodeCount={Object.keys(state.graph.nodes).length}
        edgeCount={state.graph.edges.length}
        startedAt={state.startedAt}
        finishedAt={state.finishedAt}
      />

      <main className="grid min-h-0 flex-1 gap-3 p-3 lg:grid-cols-[300px_minmax(0,1fr)_360px]">
        {/* Left: input + pipeline stages */}
        <div className="flex min-h-0 flex-col gap-3">
          <EventInputPanel
            status={state.status}
            event={event}
            model={model}
            onEventChange={setEvent}
            onModelChange={setModel}
            onRun={onRun}
            onCancel={onCancel}
            onReset={onReset}
          />
          <div className="min-h-0 flex-1 overflow-hidden">
            <PipelineStages stages={state.stages} />
          </div>
        </div>

        {/* Center: graph + bottom output tabs */}
        <div className="flex min-h-0 flex-col gap-3">
          <section className="panel relative min-h-[420px] flex-1 overflow-hidden">
            <ReactFlowProvider>
              <GraphCanvas
                graph={state.graph}
                prunedEdgeIds={state.prunedEdgeIds}
                prunedNodeIds={state.prunedNodeIds}
                selection={selection}
                onSelect={setSelection}
                flowingEdgeIds={flowingEdgeIds}
              />
            </ReactFlowProvider>
          </section>

          <section className="panel flex min-h-[260px] flex-col">
            <Tabs
              tabs={bottomTabs}
              active={bottomTab}
              onChange={(k) => setBottomTab(k as typeof bottomTab)}
              className="px-3 pt-2"
            />
            <div className="min-h-0 flex-1 overflow-y-auto">
              {bottomTab === "macro" && (
                <MacroComparePanel
                  macroNow={state.macroNow}
                  caseStudies={state.caseStudies}
                />
              )}
              {bottomTab === "portfolio" && <PortfolioPanel impacts={state.portfolio} />}
              {bottomTab === "scenarios" && (
                <ScenariosPanel
                  scenarios={state.scenarios}
                  onPickScenario={(text) => onRun(text, model)}
                />
              )}
            </div>
          </section>
        </div>

        {/* Right: inspector + log */}
        <div className="flex min-h-0 flex-col gap-3">
          <div className="min-h-0 flex-1">
            <InspectorPane
              graph={state.graph}
              selection={selection}
              caseStudies={state.caseStudies}
              debates={state.debates}
              prunedEdgeIds={state.prunedEdgeIds}
            />
          </div>
          <LogStream
            entries={state.log}
            onClear={() => dispatch({ type: "clear_log" })}
          />
        </div>
      </main>
    </div>
  );
}
