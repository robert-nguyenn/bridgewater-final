# Setup

## Prerequisites

- Python 3.12 (use `/usr/local/bin/python3.12`, not 3.13)
- `git`
- A Mac, Linux, or WSL shell

## 1. Environment

```bash
/usr/local/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev,demo]"
```

## 2. Secrets

```bash
cp .env.example .env
```

Then edit `.env`:

| Var | Where to get it | Required |
|-----|------------------|----------|
| `ANTHROPIC_API_KEY` | https://console.anthropic.com/ | yes |
| `FRED_API_KEY` | https://fred.stlouisfed.org/docs/api/api_key.html | yes |
| `HF_TOKEN` | https://huggingface.co/settings/tokens | yes |
| `PERPLEXITY_API_KEY` | https://www.perplexity.ai/settings/api | no, stretch only |

## 3. HuggingFace login

`datasets.load_dataset` needs a logged-in user for the gated mirrors:

```bash
huggingface-cli login
```

Paste the same token you put in `.env`.

## 4. Smoke test

```bash
python -m src.orchestrator --event "25% tariff on Chinese semiconductors" --dry-run
```

Expected: pipeline scaffolding wires together, no real API calls.

For a real run drop `--dry-run`. First run will populate `.cache/` and `logs/<run_id>/`.

## 5. Tests

```bash
pytest -x
```

Per-module smoke tests run in under 30 seconds against fixtures in `tests/fixtures/`.

## Common issues

- **`fredapi` 403:** check the key. FRED is generous on rate limits, but a missing key fails fast.
- **`datasets` auth error:** rerun `huggingface-cli login`, the token in `.env` is for the SDK, the CLI login is separate.
- **`yfinance` empty frame:** Yahoo throttles. Retry, or rely on the cached fixture in tests.
