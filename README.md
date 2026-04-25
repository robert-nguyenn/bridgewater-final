# Policy Impact Scenario Mapper

Maps a plain-English policy event to a structured causal DAG of affected economic channels and markets, with per-edge sensitivity and confidence scores grounded in FRED point estimates, company fundamentals, and historical analogs.

Built for the Bridgewater AI Hackathon. The tool does not call direction. It maps what is affected, through which mechanism, how sensitive each link is, and how today's macro conditions differ from the closest historical precedent.

## Quickstart

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# fill ANTHROPIC_API_KEY, FRED_API_KEY, HF_TOKEN

huggingface-cli login

python -m src.orchestrator --event "25% tariff on Chinese semiconductors" --dry-run
```

See [setup.md](setup.md) for full setup, and [CLAUDE.md](CLAUDE.md) for architecture, module ownership, and conventions.

## Demo

```bash
streamlit run demo/app.py
```

## Layout

- `src/types.py` typed contracts shared across modules
- `src/orchestrator.py` 10-stage pipeline
- `src/agents/` one file per agent
- `src/tools/` FRED, Yahoo, HF wrappers with caching
- `src/viz/` graph rendering
- `prompts/` one markdown per agent, version controlled
- `tests/` smoke and golden tests, fixtures in `tests/fixtures/`
- `demo/app.py` Streamlit frontend
