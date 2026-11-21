"""Cost calculator and usage monitor.

Every model call is recorded in the `llm_calls` table with its tokens (input, cached input, output, thinking), its
latency, the API key slot that served it and its cost at the price card in force. This module turns that ledger into
the numbers an operator needs: what was spent and on what, how well the prompt cache is working, how fast money is
going out right now, and what a planned amount of work would cost.

    python -m murray.cli usage                 # the live report
    python -m murray.cli cost gemini-4-neon --in 8000 --cached 3500 --out 600 --thought 2500
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from . import config
from .db import DB

MODELS = [config.HYDROGEN, config.NEON, config.ARGON, config.CARBON]


def price_table(when: datetime | None = None) -> list[dict]:
    """USD per 1M tokens for each model: the card in force, and the next scheduled change if there is one."""
    when = when or datetime.now(timezone.utc)
    day = when.strftime("%Y-%m-%d")
    out = []
    for model in MODELS:
        card = config.price_card(model, when)
        nxt = next((c for c in config.PRICES.get(model, []) if c["start"] > day), None)
        out.append({"model": model, "input": card["inp"], "cached_input": card["cached"], "output": card["out"],
                    "since": card["start"], "next": ({"from": nxt["start"], "input": nxt["inp"], "cached_input": nxt["cached"],
                                                      "output": nxt["out"]} if nxt else None)})
    return out


def calc(model: str, inp: int = 0, out: int = 0, thought: int = 0, cached: int = 0, when: datetime | None = None) -> dict:
    """The cost of one call. `inp` includes `cached`; thinking tokens are billed at the output rate."""
    card = config.price_card(model, when)
    fresh = max(inp - cached, 0)
    parts = {"fresh_input": fresh * card["inp"] / 1e6, "cached_input": cached * card["cached"] / 1e6,
             "output": out * card["out"] / 1e6, "thinking": thought * card["out"] / 1e6}
    return {"model": model, "usd": round(sum(parts.values()), 6), "breakdown": {k: round(v, 6) for k, v in parts.items()},
            "without_cache_usd": round((inp * card["inp"] + (out + thought) * card["out"]) / 1e6, 6),
            "prices_per_1m": {"input": card["inp"], "cached_input": card["cached"], "output": card["out"]}}


def _rows(db: DB, group: str, since: float) -> list[dict]:
    rows = db.q(
        f"SELECT {group} AS k, COUNT(*) calls, SUM(status IN ('error')) errors, SUM(status='in_flight') in_flight, "
        "COALESCE(SUM(input_tokens),0) tin, COALESCE(SUM(cached_tokens),0) tcached, COALESCE(SUM(output_tokens),0) tout, "
        "COALESCE(SUM(thought_tokens),0) tthink, COALESCE(SUM(cost_usd),0) usd, AVG(CASE WHEN status NOT IN ('error','in_flight') THEN latency_ms END) avg_ms "
        f"FROM llm_calls WHERE ts_start>=? GROUP BY {group} ORDER BY usd DESC", (since,))
    out = []
    for r in rows:
        tin = int(r["tin"] or 0)
        out.append({"key": r["k"], "calls": r["calls"], "errors": int(r["errors"] or 0), "in_flight": int(r["in_flight"] or 0),
                    "input_tokens": tin, "cached_tokens": int(r["tcached"] or 0), "output_tokens": int(r["tout"] or 0),
                    "thinking_tokens": int(r["tthink"] or 0), "usd": round(float(r["usd"] or 0), 4),
                    "cache_hit_pct": round(100.0 * (r["tcached"] or 0) / tin, 1) if tin else 0.0,
                    "avg_latency_s": round((r["avg_ms"] or 0) / 1000, 1)})
    return out


def _window(db: DB, seconds: float) -> dict:
    since = time.time() - seconds
    r = db.one("SELECT COUNT(*) calls, COALESCE(SUM(cost_usd),0) usd, COALESCE(SUM(input_tokens+output_tokens+thought_tokens),0) tokens, "
               "SUM(status='error') errors FROM llm_calls WHERE ts_start>=?", (since,))
    return {"calls": r["calls"], "usd": round(float(r["usd"]), 4), "tokens": int(r["tokens"]), "errors": int(r["errors"] or 0)}


def _saved_by_cache(db: DB, since: float) -> float:
    saved = 0.0
    for r in db.q("SELECT model, COALESCE(SUM(cached_tokens),0) c FROM llm_calls WHERE ts_start>=? GROUP BY model", (since,)):
        card = config.price_card(r["model"])
        saved += (r["c"] or 0) * (card["inp"] - card["cached"]) / 1e6
    return round(saved, 4)


def _percentiles(db: DB, since: float) -> dict:
    out: dict[str, dict] = {}
    for r in db.q("SELECT role, latency_ms FROM llm_calls WHERE ts_start>=? AND status NOT IN ('error','in_flight') AND latency_ms IS NOT NULL", (since,)):
        out.setdefault(r["role"], {"v": []})["v"].append(r["latency_ms"])
    res = {}
    for role, d in out.items():
        v = sorted(d["v"])
        res[role] = {"n": len(v), "p50_s": round(v[len(v) // 2] / 1000, 1), "p95_s": round(v[min(len(v) - 1, int(len(v) * 0.95))] / 1000, 1)}
    return res


def _per_role_average(db: DB, since: float) -> dict:
    """What one call of each role costs on average: the building blocks for estimates."""
    out = {}
    for r in db.q("SELECT role, COUNT(*) n, AVG(cost_usd) usd, AVG(input_tokens) tin, AVG(cached_tokens) tc, AVG(output_tokens) tout, "
                  "AVG(thought_tokens) tth, AVG(latency_ms) ms FROM llm_calls WHERE ts_start>=? AND status NOT IN ('error','in_flight') GROUP BY role", (since,)):
        out[r["role"]] = {"n": r["n"], "usd": round(r["usd"] or 0, 5), "input_tokens": int(r["tin"] or 0), "cached_tokens": int(r["tc"] or 0),
                          "output_tokens": int(r["tout"] or 0), "thinking_tokens": int(r["tth"] or 0), "latency_s": round((r["ms"] or 0) / 1000, 1)}
    return out


# What a unit of work is made of, in calls per role. Used with the measured per-role averages (or the configured
# estimates when a role has not run yet) to price work before it is done.
RECIPES = {
    "chat turn answered locally": {"gate": 1, "broca": 1},
    "chat turn through the executive": {"gate": 1, "neon": 1, "broca": 1},
    "chat turn with an uploaded image": {"gate": 1, "argon": 1, "neon": 1, "broca": 1},
    "chat turn with a render fixed once": {"gate": 1, "neon": 2, "worker": 2, "argon": 1, "argon_hi": 1, "broca": 1},
    "one sleep (consolidation)": {"carbon": 1},
    "one upbringing day (12 ticks)": {"gate": 8, "neon": 8, "broca": 9, "worker": 5, "argon": 3, "argon_hi": 2, "carbon": 1},
    "single-model baseline answer (Hydrogen)": {"base_h": 1},
    "single-model baseline answer (Neon)": {"base_n": 1},
}


def estimate(db: DB | None = None, since: float = 0.0) -> list[dict]:
    measured = _per_role_average(db, since) if db is not None else {}
    out = []
    for name, recipe in RECIPES.items():
        usd, secs, basis = 0.0, 0.0, "measured"
        for role, n in recipe.items():
            if role in measured:
                usd += n * measured[role]["usd"]
                secs += n * measured[role]["latency_s"]
            else:
                usd += n * config.ROLES[role].est_cost_usd
                basis = "partly estimated"
        out.append({"unit": name, "usd": round(usd, 4), "model_seconds": round(secs, 1), "calls": sum(recipe.values()), "basis": basis})
    return out


def usage(db: DB, minutes: float | None = None) -> dict:
    """The usage report. `minutes` limits it to the recent past; None means everything recorded."""
    since = time.time() - minutes * 60 if minutes else 0.0
    by_model = _rows(db, "model", since)
    totals = {"calls": sum(r["calls"] for r in by_model), "errors": sum(r["errors"] for r in by_model),
              "input_tokens": sum(r["input_tokens"] for r in by_model), "cached_tokens": sum(r["cached_tokens"] for r in by_model),
              "output_tokens": sum(r["output_tokens"] for r in by_model), "thinking_tokens": sum(r["thinking_tokens"] for r in by_model),
              "usd": round(sum(r["usd"] for r in by_model), 4)}
    totals["cache_hit_pct"] = round(100.0 * totals["cached_tokens"] / totals["input_tokens"], 1) if totals["input_tokens"] else 0.0
    totals["saved_by_cache_usd"] = _saved_by_cache(db, since)
    w10, w60, wday = _window(db, 600), _window(db, 3600), _window(db, 86400)
    bucket = 300
    start = int(time.time() // bucket * bucket) - 36 * bucket
    line = {start + i * bucket: {"calls": 0, "usd": 0.0, "tokens": 0} for i in range(37)}
    for r in db.q("SELECT CAST(ts_start/? AS INTEGER)*? b, COUNT(*) calls, COALESCE(SUM(cost_usd),0) usd, "
                  "COALESCE(SUM(input_tokens+output_tokens+thought_tokens),0) tokens FROM llm_calls WHERE ts_start>=? GROUP BY b", (bucket, bucket, start)):
        if r["b"] in line:
            line[r["b"]] = {"calls": r["calls"], "usd": round(float(r["usd"]), 4), "tokens": int(r["tokens"])}
    first = db.val("SELECT MIN(ts_start) FROM llm_calls", (), None)
    return {
        "account": config.ACCOUNT or None, "keys": len(config.API_KEYS),
        "since": datetime.fromtimestamp(since or first or time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "totals": totals, "by_model": by_model, "by_role": _rows(db, "role", since), "by_context": _rows(db, "context", since),
        "by_key": [dict(r, key=("key #%d" % (r["key"] + 1)) if r["key"] is not None else "earlier key") for r in _rows(db, "key_slot", since)],
        "latency": _percentiles(db, since),
        "windows": {"last_10_min": w10, "last_hour": w60, "last_24_h": wday},
        "projection": {"usd_per_hour_at_10min_rate": round(w10["usd"] * 6, 2), "usd_per_day_at_hour_rate": round(w60["usd"] * 24, 2)},
        "timeline": [{"t": datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M"), **v} for t, v in sorted(line.items())],
        "per_role_average": _per_role_average(db, since), "estimates": estimate(db, since), "prices": price_table(),
    }


def format_report(u: dict, guards: dict | None = None) -> str:
    """The report as plain text, for the terminal."""
    t = u["totals"]
    lines = [f"Usage since {u['since']}" + (f"  (account: {u['account']}, {u['keys']} key(s))" if u.get("account") else f"  ({u['keys']} key(s))"),
             f"  calls {t['calls']}  errors {t['errors']}  cost ${t['usd']:.2f}  tokens in {t['input_tokens']:,} (cached {t['cached_tokens']:,} = {t['cache_hit_pct']}%) "
             f"out {t['output_tokens']:,} thinking {t['thinking_tokens']:,}  saved by cache ${t['saved_by_cache_usd']:.2f}",
             f"  last 10 min: {u['windows']['last_10_min']['calls']} calls ${u['windows']['last_10_min']['usd']:.2f}   last hour: "
             f"{u['windows']['last_hour']['calls']} calls ${u['windows']['last_hour']['usd']:.2f}   last 24 h: ${u['windows']['last_24_h']['usd']:.2f}",
             f"  at this rate: ${u['projection']['usd_per_hour_at_10min_rate']:.2f} per hour, ${u['projection']['usd_per_day_at_hour_rate']:.2f} per day", ""]
    for title, rows in (("by model", u["by_model"]), ("by role", u["by_role"]), ("by context", u["by_context"]), ("by key", u["by_key"])):
        lines.append(f"  {title:11} {'calls':>6} {'err':>4} {'input':>10} {'cached%':>8} {'output':>8} {'thinking':>9} {'usd':>8} {'avg s':>6}")
        for r in rows:
            lines.append(f"    {str(r['key'])[:20]:20} {r['calls']:>6} {r['errors']:>4} {r['input_tokens']:>10,} {r['cache_hit_pct']:>7}% "
                         f"{r['output_tokens']:>8,} {r['thinking_tokens']:>9,} {r['usd']:>8.3f} {r['avg_latency_s']:>6}")
        lines.append("")
    lines.append("  what a unit of work costs (from measured averages):")
    for e in u["estimates"]:
        lines.append(f"    {e['unit']:42} ${e['usd']:.4f}  {e['calls']} calls  ({e['basis']})")
    if guards:
        b = guards.get("breaker")
        lines += ["", "  runaway guard: " + (f"OPEN since {b['at']}: {b['reason']}" if b else "closed (normal)"),
                  f"    10 min: ${guards['last_10min_usd']:.2f} of ${guards['breaker_usd_per_10min']:.0f}, {guards['last_10min_calls']} of {guards['breaker_calls_per_10min']} calls"
                  f"   24 h: ${guards['last_24h_usd']:.2f} of ${guards['breaker_usd_per_day']:.0f}   public 24 h: ${guards['public_24h_usd']:.2f} of ${guards['breaker_public_usd_per_day']:.0f}"]
    return "\n".join(lines)
