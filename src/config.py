from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.getenv("CACHE_DIR", REPO_ROOT / ".cache"))
LOGS_DIR = REPO_ROOT / "logs"
PROMPTS_DIR = REPO_ROOT / "prompts"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"

CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

MODEL = "claude-opus-4-7"
MODEL_FAST = "claude-sonnet-4-6"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
HF_TOKEN = os.getenv("HF_TOKEN", "")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
