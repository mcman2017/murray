"""Settings, read once from the environment. Nothing here is secret except what the environment supplies."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _envi(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# ---------------------------------------------------------------- models and thinking levels

HYDROGEN = _env("MURRAY_MODEL_HYDROGEN", "gemini-4-hydrogen")
NEON = _env("MURRAY_MODEL_NEON", "gemini-4-neon")
ARGON = _env("MURRAY_MODEL_ARGON", "gemini-4-argon")
CARBON = _env("MURRAY_MODEL_CARBON", "gemini-4-carbon")

# Levels each model accepts. A requested level is clamped down to the highest allowed one.
LEVEL_ORDER = ["low", "medium", "high", "xhigh"]
ALLOWED_LEVELS = {
    HYDROGEN: ["low", "medium", "high", "xhigh"],
    NEON: ["low", "medium", "high", "xhigh"],
    ARGON: ["low", "medium", "high"],
    CARBON: ["low", "medium", "high"],
}


@dataclass(frozen=True)
class Role:
    """One cognitive role: which model plays it, how hard it thinks, and its budgets."""

    name: str
    model: str
    level: str | None          # None = the model's own default (used only by the single-model baselines)
    max_output_tokens: int     # covers thinking + answer
    timeout_s: int             # one HTTP call
    deadline_s: int            # all attempts together
    store: bool                # must later calls chain from this one?
    est_cost_usd: float        # used by the spend limiter before the real cost is known


ROLES: dict[str, Role] = {
    r.name: r
    for r in [
        # Hydrogen: salience network, subconscious workers, Broca. Kept at low/medium on purpose.
        Role("gate", HYDROGEN, "low", 4096, 60, 120, False, 0.006),
        Role("worker", HYDROGEN, "medium", 16384, 150, 300, True, 0.025),
        Role("broca", HYDROGEN, "medium", 8192, 120, 240, True, 0.015),
        # Neon: the Global Workspace arbitrator.
        Role("neon", NEON, "high", 24576, 240, 420, True, 0.06),
        Role("neon_x", NEON, "xhigh", 49152, 420, 600, True, 0.14),
        # Argon: the visual critic. Google recommends >= 32,768 output tokens for Argon and Carbon.
        Role("argon", ARGON, "medium", 32768, 240, 420, False, 0.035),
        Role("argon_hi", ARGON, "high", 32768, 300, 480, False, 0.05),
        # Carbon: sleep consolidation, awake deep contemplation, and the blinded eval judge.
        Role("carbon", CARBON, "high", 32768, 600, 900, False, 0.15),
        Role("judge", CARBON, "medium", 32768, 300, 480, False, 0.04),
        # Single-model baselines: model-default thinking, no architecture.
        # generous deadlines: at default thinking a single model can deliberate for many minutes on a render task
        Role("base_h", HYDROGEN, None, 32768, 900, 1200, False, 0.03),
        Role("base_n", NEON, None, 32768, 900, 1200, False, 0.07),
    ]
}


def clamp_level(model: str, level: str | None) -> str | None:
    if level is None:
        return None
    allowed = ALLOWED_LEVELS.get(model, LEVEL_ORDER)
    if level in allowed:
        return level
    idx = LEVEL_ORDER.index(level) if level in LEVEL_ORDER else 1
    for cand in reversed(LEVEL_ORDER[: idx + 1]):
        if cand in allowed:
            return cand
    return allowed[0]


# ---------------------------------------------------------------- prices (USD per 1M tokens)

# Each model has one or more cards; the card in force is the last whose `start` is not after the call.
# `out` covers the answer and the thinking alike. `cached` is the implicit-cache rate (95 % off).
PRICES: dict[str, list[dict]] = {
    HYDROGEN: [{"start": "2026-01-01", "inp": 1.50, "cached": 0.075, "out": 7.50}],
    NEON: [{"start": "2026-01-01", "inp": 3.00, "cached": 0.15, "out": 15.00}],
    ARGON: [
        {"start": "2026-01-01", "inp": 2.00, "cached": 0.10, "out": 10.00},
        {"start": "2026-12-01", "inp": 4.00, "cached": 0.20, "out": 20.00},
    ],
    CARBON: [
        {"start": "2026-01-01", "inp": 2.00, "cached": 0.10, "out": 10.00},
        {"start": "2026-12-01", "inp": 4.00, "cached": 0.20, "out": 20.00},
    ],
}


def price_card(model: str, when: datetime | None = None) -> dict:
    when = when or datetime.now(timezone.utc)
    cards = PRICES.get(model) or PRICES[HYDROGEN]
    day = when.strftime("%Y-%m-%d")
    chosen = cards[0]
    for card in cards:
        if card["start"] <= day:
            chosen = card
    return chosen


def cost_usd(model: str, inp: int, out: int, thought: int, cached: int, when: datetime | None = None) -> float:
    card = price_card(model, when)
    fresh = max(inp - cached, 0)
    return (fresh * card["inp"] + cached * card["cached"] + (out + thought) * card["out"]) / 1_000_000


# ---------------------------------------------------------------- paths and runtime

ROLE = _env("MURRAY_ROLE", "app")                      # app | sandbox
DATA_DIR = Path(_env("MURRAY_DATA_DIR", "/data"))
DB_PATH = Path(_env("MURRAY_DB_PATH", str(DATA_DIR / "murray.db")))
RENDERS_DIR = DATA_DIR / "renders"
UPLOADS_DIR = DATA_DIR / "uploads"
EVAL_DIR = DATA_DIR / "eval"
ARCHIVE_DIR = DATA_DIR / "archive"
APP_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = Path(_env("MURRAY_WEB_DIR", str(APP_DIR / "web")))
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

HOST = _env("MURRAY_HOST_BIND", "0.0.0.0")
PORT = _envi("MURRAY_PORT", 8000)
SANDBOX_PORT = _envi("MURRAY_SANDBOX_PORT", 8100)
SANDBOX_URL = _env("MURRAY_SANDBOX_URL", "")           # empty = in-process sandbox (mock mode only)

def _api_keys() -> list[str]:
    """Every distinct key in GEMINI_API_KEY_1, _2, ... then GEMINI_API_KEY, then GOOGLE_API_KEY."""
    numbered = sorted((int(m.group(1)), v) for k, v in os.environ.items() if (m := re.fullmatch(r"GEMINI_API_KEY_(\d+)", k)))
    out: list[str] = []
    for value in [v for _, v in numbered] + [os.environ.get("GEMINI_API_KEY", ""), os.environ.get("GOOGLE_API_KEY", "")]:
        value = value.strip()
        if value and value not in out:
            out.append(value)
    return out


API_KEYS = _api_keys()
ACCOUNT = os.environ.get("GEMINI_ACCOUNT", "").strip()      # a label for the operator: whose quota is being spent
API_KEY = API_KEYS[0] if API_KEYS else ""
LLM_MODE = _env("MURRAY_LLM_MODE", "live" if API_KEY else "mock")   # live | mock
ADMIN_TOKEN = os.environ.get("MURRAY_ADMIN_TOKEN", "")
AUTOSTART = _env("MURRAY_AUTOSTART", "off")            # off | quick | full

# ---------------------------------------------------------------- cognition

IGNITE = _envi("MURRAY_IGNITE", 6)                    # salience needed to wake Neon
REFRACTORY_BOOST = 2                                   # extra salience an unrelated stimulus needs while refractory
SPWR = 7                                               # importance at which an episode is tagged for sleep replay
TICKS_PER_DAY = 12
MAX_RENDERS = 3                                        # render attempts per task (upbringing, eval, admin)
MAX_RENDERS_VISITOR = 2
BROCA_CHAIN_DEPTH = 6                                  # routine-chatter replies chained before a fresh start
TURN_DEADLINE_S = 180
PERSONA_NARRATIVE_CHARS = 700
PERSONA_MAX_CHARS = 2600
STUCK_STREAK = 3                                       # consecutive stuck ticks on one conflict before Carbon contemplates
# Neon may think at xhigh only in a turn that can afford it. Measured: 74 to 130 s on a suspected injection,
# 130 to 180 s on pushback, and once 325 s on a render request (which no longer escalates at all).
XHIGH_MIN_BUDGET_S = int(os.environ.get("MURRAY_XHIGH_MIN_BUDGET_S", "300"))
XHIGH_MIN_BUDGET_SECURITY_S = int(os.environ.get("MURRAY_XHIGH_MIN_BUDGET_SECURITY_S", "150"))

BANNED_PHRASES = [
    "as an ai", "as a language model", "as an artificial intelligence", "i'd be happy to help", "i would be happy to help",
    "i'm happy to help", "that's a great point", "great question", "that's a great question", "i apologize for any confusion",
    "you're absolutely right", "you are absolutely right", "certainly!", "of course!", "i hope this helps",
    "feel free to ask", "is there anything else i can help",
]

# ---------------------------------------------------------------- spending guards (USD)

# Optional caps. 0 means "no cap": on a high-tier key there is no reason to slow honest work down.
BUILD_BUDGET_USD = _envf("MURRAY_BUILD_BUDGET_USD", 0.0)           # upbringing + eval + preflight, lifetime
PUBLIC_DAILY_CAP_USD = _envf("MURRAY_PUBLIC_DAILY_CAP_USD", 0.0)   # visitor traffic, rolling 24 h
SPEND_RATE_USD_PER_10MIN = _envf("MURRAY_SPEND_RATE_USD_PER_10MIN", 0.0)
VISITOR_RATE_USD_PER_10MIN = _envf("MURRAY_VISITOR_RATE_USD_PER_10MIN", 0.0)
QUIET_RATE_USD_PER_10MIN = _envf("MURRAY_QUIET_RATE_USD_PER_10MIN", 1.0)
RATE_LIMIT_PAUSE_S = 45
RATE_LIMIT_MAX_PAUSES = 12

# The runaway guard: a circuit breaker. It never slows normal work; it exists so that a loop that has gone wrong, or
# a flood of traffic, cannot spend everything. When a threshold is crossed every model call is refused and
# background runs pause until an operator resets the breaker (`python -m murray.cli breaker reset`).
BREAKER_USD_PER_10MIN = _envf("MURRAY_BREAKER_USD_PER_10MIN", 30.0)
BREAKER_CALLS_PER_10MIN = _envi("MURRAY_BREAKER_CALLS_PER_10MIN", 2500)
BREAKER_USD_PER_DAY = _envf("MURRAY_BREAKER_USD_PER_DAY", 300.0)
BREAKER_USD_TOTAL = _envf("MURRAY_BREAKER_USD_TOTAL", 0.0)                 # lifetime ceiling; 0 = off
# Anonymous traffic has its own rolling ceiling. Reaching it rests the public chat (it recovers by itself as the
# window moves); it does not trip the breaker or touch background and operator work.
BREAKER_PUBLIC_USD_PER_DAY = _envf("MURRAY_BREAKER_PUBLIC_USD_PER_DAY", 100.0)

# Carry the static prompt prefix in a stored anchor interaction so that it is served from the implicit cache.
ANCHORING = _env("MURRAY_ANCHORING", "on") != "off"

# Quiet windows: periods in which background runs pause and visitor traffic is held to the quiet rate, for
# deployments that share an API key with something else. Format, entries separated by ';':
#   <IANA zone>|<daily | mon..sun | YYYY-MM-DD>|<HH:MM>-<HH:MM>
QUIET_WINDOWS = _env("MURRAY_QUIET_WINDOWS", "")

_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def in_quiet_window(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    for entry in filter(None, (e.strip() for e in QUIET_WINDOWS.split(";"))):
        try:
            zone, day, span = entry.split("|")
            local = now.astimezone(ZoneInfo(zone))
            start_s, end_s = span.split("-")
            start = int(start_s[:2]) * 60 + int(start_s[3:5])
            end = int(end_s[:2]) * 60 + int(end_s[3:5])
            minute = local.hour * 60 + local.minute
            day = day.strip().lower()
            day_ok = day == "daily" or day == _DAYS[local.weekday()] or day == local.strftime("%Y-%m-%d")
            if day_ok and start <= minute < end:
                return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------- public-site limits

SESSION_TURNS_PER_HOUR = _envi("MURRAY_SESSION_TURNS_PER_HOUR", 30)
SESSION_RENDERS_PER_HOUR = _envi("MURRAY_SESSION_RENDERS_PER_HOUR", 6)
IP_TURNS_PER_10MIN = _envi("MURRAY_IP_TURNS_PER_10MIN", 12)
MAX_INPUT_CHARS = 4000
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_UPLOAD_PIXELS = 16_000_000
MAX_CONCURRENT_TURNS = _envi("MURRAY_MAX_CONCURRENT_TURNS", 4)
MAX_CONCURRENT_RENDERS = _envi("MURRAY_MAX_CONCURRENT_RENDERS", 2)

# ---------------------------------------------------------------- sandbox limits

SANDBOX_WALL_S = 45
SANDBOX_WALL_FRAMES_S = 90
SANDBOX_CPU_S = 80
SANDBOX_MEM_BYTES = 3 * 1024**3
SANDBOX_FSIZE_BYTES = 32 * 1024**2
SANDBOX_MAX_PIXELS = 2048 * 2048
SANDBOX_MAX_FRAMES = 40
SANDBOX_MAX_CODE_CHARS = 20000
