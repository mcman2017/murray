"""The accelerated upbringing: six simulated days of curriculum, each closed by a sleep.

    python -m murray.upbringing --full          # 6 days, ~72 ticks, 6 sleeps
    python -m murray.upbringing --quick-seed    # 6 compressed days, for a fast end-to-end check

Conscious ticks are sequential. Render work runs in the background as "subconscious" jobs; when one finishes, its
result becomes the next stimulus, so a defect the critic finds pulls attention back to the render. The run is
resumable: its cursor (day, item) lives in the `runs` table.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from . import config, memory
from .db import DB, dumps, loads, now, rowdict
from .llm import LLM, BudgetExceeded
from .loop import Engine, Stimulus
from .memory import CANON

CURRICULUM = Path(__file__).resolve().parent / "curriculum.json"


def load_plan(mode: str) -> list[dict]:
    days = json.loads(CURRICULUM.read_text(encoding="utf-8"))["days"]
    if mode == "quick":
        # one fractal task and one conversation per day: enough to exercise every model and six sleeps
        out = []
        for d in days[:6]:                      # the quick seed is the six planned days; day 7 is the make-up day
            frac = next(i for i in d["items"] if i["kind"] == "fractal")
            talk = next(i for i in d["items"] if i["kind"] in ("special", "godel"))
            out.append({"day": d["day"], "items": [frac, talk]})
        return out
    return days


def active_run(db: DB) -> dict | None:
    row = db.one("SELECT * FROM runs WHERE kind LIKE 'upbringing%' AND status IN ('running','paused') ORDER BY id DESC LIMIT 1")
    return rowdict(row) if row else None


def status(db: DB) -> dict:
    row = db.one("SELECT * FROM runs WHERE kind LIKE 'upbringing%' ORDER BY id DESC LIMIT 1")
    if row is None:
        return {"state": "none"}
    r = rowdict(row)
    cursor = loads(r.get("cursor_json"), {}) or {}
    return {"state": r["status"], "run_id": r["id"], "mode": r["kind"].split("_", 1)[-1], "day": cursor.get("day"),
            "item": cursor.get("item"), "ticks": db.val("SELECT COALESCE(MAX(tick),0) FROM workspace WHERE scope=?", (CANON,), 0),
            "sleeps": db.val("SELECT COUNT(*) FROM sleeps WHERE scope=? AND status='done'", (CANON,), 0),
            "started_at": r.get("started_at"), "finished_at": r.get("finished_at"), "note": r.get("note"),
            "consolidated": db.kv_get("upbringing_done") == "1"}


def day_gate(db: DB, run_id: int, estimate_usd: float) -> list[str]:
    """Checks after Day 1 of a full run. Returns the reasons to stop (empty: carry on)."""
    problems = []
    if db.val("SELECT COUNT(*) FROM preferences WHERE scope=? AND expired_at IS NULL", (CANON,), 0) < 1:
        problems.append("no preference survived the first sleep")
    fixed = db.val("SELECT COUNT(*) FROM render_jobs WHERE scope=? AND iterations>=2 AND status='pass'", (CANON,), 0)
    if fixed < 1:
        problems.append("no render was found defective and then fixed")
    if db.val("SELECT COALESCE(SUM(cached_tokens),0) FROM llm_calls WHERE run_id=?", (run_id,), 0) <= 0:
        problems.append("no cached tokens were reported")
    spent = db.val("SELECT COALESCE(SUM(cost_usd),0) FROM llm_calls WHERE run_id=?", (run_id,), 0.0)
    if spent > 2.5 * estimate_usd:
        problems.append(f"day 1 cost ${spent:.2f}, more than 2.5x the estimate")
    return problems


async def run(engine: Engine, mode: str = "full", *, resume: bool = True, gate: bool = True) -> dict:
    db = engine.db
    plan = load_plan(mode)
    kind = f"upbringing_{mode}"
    context = "upbringing" if mode == "full" else "quickseed"
    existing = active_run(db) if resume else None
    if existing and existing["kind"] == kind:
        run_id = existing["id"]
        cursor = loads(existing.get("cursor_json"), {}) or {"day": 1, "item": 0}
        db.update("runs", run_id, status="running", note="resumed")
        db.x("UPDATE render_jobs SET status='interrupted', finished_at=? WHERE scope=? AND status='open'", (now(), CANON))
    else:
        cursor = {"day": 1, "item": 0}
        run_id = db.insert("runs", kind=kind, status="running", cursor_json=dumps(cursor), config_json=dumps({"days": len(plan)}),
                           started_at=now())
    engine.emit(CANON, "upbringing", {"state": "running", "mode": mode, "run_id": run_id, "cursor": cursor})
    t0 = time.monotonic()
    pending_sleep: asyncio.Task | None = None
    try:
        # a sleep that was interrupted by a restart is run again before anything else
        db.x("DELETE FROM sleeps WHERE scope=? AND status='running'", (CANON,))
        for missed in range(1, int(cursor["day"])):
            if not db.val("SELECT 1 FROM sleeps WHERE scope=? AND day=? AND status='done'", (CANON, missed)):
                await engine.sleep(CANON, missed, context=context, run_id=run_id)
        for day_plan in plan:
            day = int(day_plan["day"])
            if day < int(cursor["day"]):
                continue
            db.kv_set("canon_day", str(day))
            items = day_plan["items"][int(cursor["item"]) if day == int(cursor["day"]) else 0:]
            index = int(cursor["item"]) if day == int(cursor["day"]) else 0
            results: asyncio.Queue = asyncio.Queue()
            in_flight = 0

            async def iteration(delegation: dict) -> None:
                try:
                    res = await engine.render_iteration(delegation, scope=CANON, context=context, run_id=run_id)
                except Exception as exc:  # noqa: BLE001 - a failed job must not stall the day
                    res = {"job_id": delegation["job_id"], "render_id": None, "iteration": 99, "sandbox_status": "error",
                           "sandbox_message": f"the render job failed: {type(exc).__name__}: {str(exc)[:200]}", "hard_fail": None,
                           "verdict": "fail", "matches_request": False, "argon": None, "view": "", "worker_interaction_id": None,
                           "image_path": None, "repeat": True, "previous_defects": ""}
                await results.put(res)

            engine.emit(CANON, "day_start", {"day": day, "items": len(items)})
            while items or in_flight:
                contemplate = False
                if not results.empty():
                    res = results.get_nowait()
                    in_flight -= 1
                    stim = engine.result_stimulus(res)
                elif items:
                    item = items.pop(0)
                    index += 1
                    stim = Stimulus(text=item["text"], source="tutor", topic=item.get("topic", ""))
                    contemplate = bool(item.get("contemplate"))
                    db.update("runs", run_id, cursor_json=dumps({"day": day, "item": index}))
                else:
                    res = await results.get()
                    in_flight -= 1
                    stim = engine.result_stimulus(res)
                async with engine.lock(CANON):
                    outcome = await engine.tick(CANON, stim, context=context, day=day, run_id=run_id,
                                                max_renders=config.MAX_RENDERS, contemplate=contemplate)
                if outcome.delegation:
                    in_flight += 1
                    asyncio.create_task(iteration(outcome.delegation))
            engine.emit(CANON, "day_end", {"day": day})
            # Sleep is offline work: it runs while the next day begins. Never two sleeps at once.
            if pending_sleep is not None:
                await pending_sleep
            pending_sleep = asyncio.create_task(engine.sleep(CANON, day, context=context, run_id=run_id))
            cursor = {"day": day + 1, "item": 0}
            db.update("runs", run_id, cursor_json=dumps(cursor))
            if mode == "full" and gate and day == 1:
                await pending_sleep
                pending_sleep = None
                problems = day_gate(db, run_id, estimate_usd=1.5)
                if problems:
                    db.update("runs", run_id, status="paused", note="day-1 gate: " + "; ".join(problems))
                    engine.emit(CANON, "upbringing", {"state": "paused", "run_id": run_id, "problems": problems})
                    return {"run_id": run_id, "state": "paused", "problems": problems}
        if pending_sleep is not None:
            await pending_sleep
    except BudgetExceeded as exc:
        db.update("runs", run_id, status="paused", note=f"budget: {exc}")
        engine.emit(CANON, "upbringing", {"state": "paused", "run_id": run_id, "problems": [str(exc)]})
        return {"run_id": run_id, "state": "paused", "problems": [str(exc)]}
    except Exception as exc:  # noqa: BLE001
        db.update("runs", run_id, status="paused", note=f"error: {type(exc).__name__}: {str(exc)[:300]}")
        engine.emit(CANON, "upbringing", {"state": "paused", "run_id": run_id, "problems": [f"{type(exc).__name__}: {str(exc)[:300]}"]})
        raise
    minutes = (time.monotonic() - t0) / 60
    db.update("runs", run_id, status="done", finished_at=now(), note=f"{minutes:.1f} min")
    if mode == "full":
        db.kv_set("upbringing_done", "1")      # canonical consolidation is frozen from here on
    engine.emit(CANON, "upbringing", {"state": "done", "mode": mode, "run_id": run_id, "minutes": round(minutes, 1)})
    return {"run_id": run_id, "state": "done", "minutes": round(minutes, 1), **summary(db)}


def summary(db: DB) -> dict:
    return {
        "ticks": db.val("SELECT COALESCE(MAX(tick),0) FROM workspace WHERE scope=?", (CANON,), 0),
        "episodes": db.val("SELECT COUNT(*) FROM episodes WHERE scope=?", (CANON,), 0),
        "ignited": db.val("SELECT COUNT(*) FROM episodes WHERE scope=? AND ignited=1", (CANON,), 0),
        "sleeps": db.val("SELECT COUNT(*) FROM sleeps WHERE scope=? AND status='done'", (CANON,), 0),
        "snapshots": [r["day"] for r in db.q("SELECT day FROM persona_snapshots WHERE scope=? ORDER BY day", (CANON,))],
        "preferences": db.val("SELECT COUNT(*) FROM preferences WHERE scope=? AND expired_at IS NULL", (CANON,), 0),
        "accomplishments": db.val("SELECT COUNT(*) FROM accomplishments WHERE scope=?", (CANON,), 0),
        "facts_live": db.val("SELECT COUNT(*) FROM facts WHERE scope=? AND expired_at IS NULL", (CANON,), 0),
        "facts_expired": db.val("SELECT COUNT(*) FROM facts WHERE scope=? AND expired_at IS NOT NULL", (CANON,), 0),
        "render_jobs": db.val("SELECT COUNT(*) FROM render_jobs WHERE scope=?", (CANON,), 0),
        "jobs_fixed": db.val("SELECT COUNT(*) FROM render_jobs WHERE scope=? AND iterations>=2 AND status='pass'", (CANON,), 0),
        "renders": db.val("SELECT COUNT(*) FROM renders r JOIN render_jobs j ON j.id=r.job_id WHERE j.scope=?", (CANON,), 0),
        "spend_usd": round(db.val("SELECT COALESCE(SUM(cost_usd),0) FROM llm_calls", (), 0.0), 3),
    }


def reset_canon(db: DB, archive_to: Path | None = None) -> None:
    """Archive the database, then clear Murray's canonical life (the spending ledger is kept)."""
    if archive_to is not None:
        db.backup_to(archive_to)
    for table in ("episodes", "episodes_fts", "workspace", "preferences", "accomplishments", "facts", "sleeps", "persona",
                  "persona_snapshots", "render_jobs", "renders", "events", "runs", "turns", "sessions", "uploads"):
        db.x(f"DELETE FROM {table}")
    for key in ("canon_day", "upbringing_done"):
        db.x("DELETE FROM kv WHERE key=?", (key,))
    db.x("DELETE FROM kv WHERE key LIKE 'sleep_cursor:%'")
    memory.snapshot(db, 0)


async def _main(argv: list[str]) -> int:
    from .sandbox.client import get_sandbox
    mode = "quick" if "--quick-seed" in argv else "full"
    db = DB(config.DB_PATH)
    engine = Engine(db, LLM(db), get_sandbox())
    out = await run(engine, mode, gate="--no-gate" not in argv)
    print(json.dumps(out, indent=1))
    return 0 if out.get("state") == "done" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
