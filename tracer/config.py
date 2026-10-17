"""Global configuration: paths, API keys, tracing limits and thresholds."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from a local, git-ignored .env file (existing env vars win)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")
DATA_DIR = ROOT / "data"
CACHE_DIR = Path(os.environ.get("TRACER_CACHE_DIR", DATA_DIR / "cache"))
REPORT_DIR = ROOT / "reports"


def env(name: str) -> str:
    """Read a key at call time so tests and the app can change it without re-import.
    Quotes pasted together with a value (KEY= "value") are dropped."""
    return os.environ.get(name, "").strip().strip('"').strip("'").strip()


# HTTP. Responses are cached on disk keyed by URL and non-secret params, so a re-run of the
# same trace costs no API calls. Set TRACER_HTTP_CACHE=0 to disable.
HTTP_TIMEOUT = 30
HTTP_CACHE = os.environ.get("TRACER_HTTP_CACHE", "1") != "0"
HTTP_CACHE_TTL_HOURS = float(os.environ.get("TRACER_HTTP_CACHE_TTL_HOURS", "24"))
# Query params and headers that carry credentials; never part of a cache key or evidence URL.
SECRET_PARAMS = ("crtfc_key",)
# SEC asks for at most ten requests per second.
MIN_INTERVAL_SEC = {"www.sec.gov": 0.12, "data.sec.gov": 0.12}

# Companies House bulk data index (built by scripts/build_ch_index.py; no account needed).
CH_INDEX_PATH = Path(os.environ.get("TRACER_CH_INDEX", DATA_DIR / "ch" / "companies_house.sqlite"))

# Tracing limits.
MAX_DEPTH = int(os.environ.get("TRACER_MAX_DEPTH", "8"))
MAX_ENTITIES = int(os.environ.get("TRACER_MAX_ENTITIES", "60"))

# Name matching (rapidfuzz WRatio on normalized names, 0-100).
# >= AUTO: link automatically as confirmed. >= REVIEW: link as estimated and add a review item.
# Below REVIEW: do not link; ask a person.
NAME_MATCH_AUTO = 97.0
NAME_MATCH_REVIEW = 88.0

# Ownership thresholds (percent of class, as disclosed by the source).
# A holder at or above FOLLOW_MIN_PCT is traced further up; CONTROL_PCT separates a
# controlling holder from dispersed ownership when only passive large-holder filings exist.
FOLLOW_MIN_PCT = 5.0
CONTROL_PCT = 25.0

# DART: years of business reports to try, newest first.
DART_LOOKBACK_YEARS = 2
DART_CORPCODE_TTL_HOURS = 24 * 7

# SEC: how far back to look for Schedule 13D/13G, and how many filing documents to open.
SEC_LOOKBACK_YEARS = 3
SEC_MAX_DOCS = 15

# LLM (document extraction only). Provider "auto" picks Gemini when a Google key is set,
# then Anthropic, else none.
LLM_PROVIDER = os.environ.get("TRACER_LLM_PROVIDER", "auto")  # auto | gemini | anthropic | none
GEMINI_MODEL = os.environ.get("TRACER_GEMINI_MODEL", "gemini-3.5-flash-lite")
ANTHROPIC_MODEL = os.environ.get("TRACER_ANTHROPIC_MODEL", "claude-haiku-5-5")
LLM_CACHE = os.environ.get("TRACER_LLM_CACHE", "1") != "0"
LLM_CACHE_DIR = Path(os.environ.get("TRACER_LLM_CACHE_DIR", DATA_DIR / "llm_cache"))

# Shown on every result screen (README §9: fixed display of the supported scope).
SUPPORTED_SCOPE_KO = ("지원 범위: 한국 상장사(DART) · 미국(SEC EDGAR) 심화, 그 외 관할은 GLEIF 회계상 모회사까지 "
                      "(영국 Companies House는 데이터 색인이 있을 때만)")
