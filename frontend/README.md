# Policy Impact Scenario Mapper — Frontend

Demo-ready React UI for the Policy Impact Scenario Mapper. Streams a 10-stage
pipeline (IdeaAgent → AnalogSearch → TreeBuilder → Sensitivity → LogicVerifier →
Adversary/Defender → MacroComparator → Pruner → Portfolio → Scenario) into an
interactive causal DAG with sensitivity-coded edges, a tabbed inspector, and
live agent logs.

The UI ships with a self-contained mock pipeline so you can demo the full flow
without standing up the Python backend. When the orchestrator is ready, swap
`runMockPipeline` in `src/lib/pipeline.ts` for a fetch / SSE / WebSocket call —
the event shape is identical.

## Quickstart

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173
```

`npm run build` does a type-check (`tsc -b`) plus a production bundle.
`npm run lint` is a type-only check.

## What you can demo

1. Pick an example chip (Semi tariff, ECB lending, OPEC cut, Fed pivot) or
   type your own policy event. Validation gates the run button.
2. Click **Map impact**. Watch the Pipeline stepper light up stage by stage
   and the DAG materialize, layer by layer, in the canvas.
3. Click any **node** to see its evidence and which historical analogs are
   attached. Click any **edge** to see the mechanism, sensitivity bar,
   confidence bar, and the Adversary/Defender transcript.
4. Switch the bottom tabs to inspect:
   - **Macro then vs now**: per-case-study divergence across CPI, PCE, fed
     funds, 10y, DXY, unemployment, GDP.
   - **Portfolio impact**: instruments grouped by asset class with bps moves
     and rationales.
   - **Tail scenarios**: clickable to feed a scenario back into the pipeline.
5. The right column shows a live run log (timestamps, level, stage, agent).
6. Drag nodes to rearrange — your positions are preserved across stream
   updates. Use the bottom-right controls or the minimap to navigate.

## Layout

```
frontend/
├── index.html
├── package.json
├── vite.config.ts
├── tailwind.config.js
├── tsconfig*.json
└── src/
    ├── App.tsx                 # 3-col shell + state wiring
    ├── main.tsx
    ├── index.css               # tailwind base + react-flow overrides
    ├── types.ts                # mirrors src/types.py (append-only)
    ├── lib/
    │   ├── format.ts           # pct / num / asset color helpers
    │   ├── stages.ts           # 10-stage spec
    │   ├── mockData.ts         # per-scenario mock bundles
    │   └── pipeline.ts         # streaming simulator + reducer
    └── components/
        ├── Header.tsx
        ├── EventInputPanel.tsx
        ├── PipelineStages.tsx
        ├── InspectorPane.tsx
        ├── LogStream.tsx
        ├── graph/
        │   ├── GraphCanvas.tsx
        │   ├── CausalNode.tsx
        │   ├── CausalEdge.tsx
        │   └── layout.ts
        ├── panels/
        │   ├── NodeInspector.tsx
        │   ├── EdgeInspector.tsx
        │   ├── EvidenceList.tsx
        │   ├── MacroComparePanel.tsx
        │   ├── PortfolioPanel.tsx
        │   └── ScenariosPanel.tsx
        └── ui/
            ├── Card.tsx
            ├── Badge.tsx
            ├── Button.tsx
            ├── ScoreBar.tsx
            └── Tabs.tsx
```

## Wiring it to the real backend

Replace the body of `runMockPipeline` with a fetch / EventSource that hits
`src/orchestrator.run_pipeline`. Each `dispatch({ type: "event", ... })` call
expects a `PipelineEvent` (see `src/lib/pipeline.ts` for the discriminated
union). The reducer (`applyEvent`) is pure and side-effect free, so any
transport that yields these events will plug in.

Suggested minimal backend bridge (FastAPI):

```python
# server.py
from fastapi import FastAPI
from sse_starlette.sse import EventSourceResponse

from src.orchestrator import run_pipeline_stream  # <- generator version

app = FastAPI()

@app.get("/run")
async def run(event: str, model: str = "claude-opus-4-7"):
    async def stream():
        for evt in run_pipeline_stream(event, model=model):
            yield {"event": "message", "data": evt.json()}
    return EventSourceResponse(stream())
```

Then on the frontend, swap `runMockPipeline` for an `EventSource("/run?...")`
loop that JSON-parses each message and forwards it to the same dispatcher.

## Color & sensitivity conventions

- Node accent strip = asset class (see legend on the canvas).
- Edge stroke width = sensitivity (thicker = stronger transmission).
- Edge stroke color = sensitivity bin (greys for low, blue gradient for high).
- Edge label color = confidence (red → amber → blue → green).
- Pruned edges/nodes render dashed and dimmed but stay visible for audit.

## Notes for the hackathon demo

- The stage tracker shows skipped stages (e.g. layer-3 expansion if no analog
  warranted it) explicitly — useful when answering "why did you not push
  this further".
- The Inspector keeps the adversary transcript visible so you can answer
  "why did you keep this edge" with one click.
- The default pacing is ~12s end-to-end. Tune `speed` in
  `runMockPipeline({ ..., speed })` for slower/faster demos.
