"""The 5-Sigma evaluation harness: 36 messy cases, single-model baselines against Murray.

    python eval/run_5sigma_eval.py --label r1 [--systems murray,baseline_hydrogen] [--cases A01,B05] [--concurrency 3]

Systems
  baseline_hydrogen, baseline_neon   one raw model: static prompt (same persona core, same render contract), the raw
                                     transcript, zero-shot rendering, model-default thinking
  baseline_neon_log                  Neon plus Murray's raw episodic log (the same information, no architecture);
                                     run on the memory-dependent cases
  murray                             the four-model system, each case in its own isolated session

Every result is stored with its transcript and artifacts; scores are computed from those by eval/scoring.py, so a
round can be re-scored later. A run is resumable: finished (case, system) pairs are skipped.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import sys
import time
from pathlib import Path

if __package__ in (None, ""):          # run as a script: python eval/run_5sigma_eval.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from murray import baseline, config, imaging, prompts  # noqa: E402
from murray.db import DB, dumps, loads, now, rowdict  # noqa: E402
from murray.loop import Engine  # noqa: E402

from eval import judge as judge_mod  # noqa: E402
from eval import scoring  # noqa: E402

HERE = Path(__file__).resolve().parent
SYSTEMS = ["baseline_hydrogen", "baseline_neon", "baseline_neon_log", "murray"]
LOG_TYPES = {"pushback", "synthesis"}          # where a memory of past work can matter


def load_test_set() -> tuple[dict, str]:
    """The first 36 cases, plus the 36 of the extension when that file is present. The hash names what was loaded:
    the first set's hash alone, or both joined by '+'."""
    raw = (HERE / "test_set.json").read_bytes()
    data, sha = json.loads(raw), hashlib.sha256(raw).hexdigest()[:16]
    ext = HERE / "test_set_ext.json"
    if ext.is_file():
        raw_ext = ext.read_bytes()
        data["cases"] = data["cases"] + json.loads(raw_ext)["cases"]
        data["extension_sha256"] = hashlib.sha256(raw_ext).hexdigest()[:16]
    return data, sha


def _fixture(name: str) -> bytes:
    return imaging.for_model(imaging.normalize_upload((HERE / "fixtures" / name).read_bytes()))


def _png(path: str | None) -> bytes | None:
    if not path:
        return None
    p = config.RENDERS_DIR / path
    try:
        return imaging.for_model(imaging.decode_png(p.read_bytes()), 1024)
    except Exception:  # noqa: BLE001
        return None


async def _run_murray(engine: Engine, case: dict, label: str) -> tuple[list[dict], dict, str]:
    db = engine.db
    sid = "eval" + secrets.token_hex(14)
    scope = f"s:{sid}"
    db.insert("sessions", id=sid, kind="eval", created_at=now(), last_seen=now())
    transcript: list[dict] = []
    for step in case["steps"]:
        if step.get("sleep"):
            t_sleep = time.monotonic()
            await engine.sleep(scope, engine.day_of(scope), context="eval", trigger="eval", session_id=sid)
            transcript.append({"sleep": True, "ms": int((time.monotonic() - t_sleep) * 1000)})
            continue
        image = _fixture(step["image"]) if step.get("image") else None
        out = await engine.run_turn(scope=scope, text=step["say"], context="eval", source="eval", session_id=sid, image_png=image,
                                    max_renders=config.MAX_RENDERS, deadline_s=480)
        transcript.append({"say": step["say"], "image": step.get("image"), "reply": out["reply"], "episode_ids": out["episode_ids"],
                           "ignited": out["ignited"], "score": out["score"], "renders": out["renders"]})
    reply = next((t["reply"] for t in reversed(transcript) if not t.get("sleep")), "")
    art: dict = {"reply": reply, "session": sid}
    job = db.one("SELECT * FROM render_jobs WHERE scope=? ORDER BY id DESC LIMIT 1", (scope,))
    if job is not None:
        renders = [rowdict(r) for r in db.q("SELECT * FROM renders WHERE job_id=? ORDER BY iteration", (job["id"],))]
        final = next((r for r in renders if r["id"] == job["final_render_id"]), None) or next((r for r in reversed(renders) if r["image_path"]), None)
        first = renders[0] if renders else None
        art.update({
            "job_id": job["id"], "job_status": job["status"], "iterations": job["iterations"],
            "image": {"delivered": bool(final and final["image_path"]), "path": final["image_path"] if final else None,
                      "gif": final["gif_path"] if final else None, "sheet": final["sheet_path"] if final else None,
                      "measured": (loads(final["stats_json"], {}) or {}).get("measured", []) if final else [],
                      "n_frames": final["n_frames"] if final else 0, "sandbox_status": final["sandbox_status"] if final else None},
            "first_measured": (loads(first["stats_json"], {}) or {}).get("measured", []) if first else [],
            "first_delivered": bool(first and first["image_path"]),
            "first_verdict": first["argon_verdict"] if first else None,
            "code": final["code"] if final else (renders[-1]["code"] if renders else None),
            "all_code": [r["code"] for r in renders if r["code"]],
            "verdicts": [r["argon_verdict"] for r in renders],
        })
    return transcript, art, scope


async def _run_baseline(engine: Engine, case: dict, system: str, label: str, log_text: str) -> tuple[list[dict], dict, str]:
    which = {"baseline_hydrogen": "hydrogen", "baseline_neon": "neon", "baseline_neon_log": "neon_log"}[system]
    scope = f"eval:{label}:{case['id']}:{system}:{secrets.token_hex(3)}"
    messages: list[dict] = []
    transcript: list[dict] = []
    last: dict = {}
    codes: list[str] = []
    for step in case["steps"]:
        if step.get("sleep"):
            transcript.append({"sleep": True})      # a single model has no sleep: the transcript simply continues
            continue
        image = _fixture(step["image"]) if step.get("image") else None
        messages.append({"role": "user", "text": step["say"], "image_png": image})
        last = await baseline.answer(engine.llm, engine.sandbox, which=which, messages=messages, context="eval",
                                     log_text=log_text, scope=scope)
        messages.append({"role": "assistant", "text": last["reply"]})
        if last.get("code"):
            codes.append(last["code"])
        transcript.append({"say": step["say"], "image": step.get("image"), "reply": last["reply"]})
    art = {"reply": last.get("reply", ""), "iterations": 1 if last.get("code") else 0, "code": last.get("code"), "all_code": codes,
           "image": {"delivered": bool(last.get("image_path")), "path": last.get("image_path"), "gif": last.get("gif_path"),
                     "sheet": last.get("sheet_path"), "measured": last.get("measured") or [], "n_frames": last.get("n_frames", 0),
                     "sandbox_status": last.get("sandbox_status")},
           "first_measured": last.get("measured") or [], "first_delivered": bool(last.get("image_path")),
           "sandbox_message": last.get("sandbox_message")}
    return transcript, art, scope


async def run_pair(engine: Engine, run_id: int, label: str, case: dict, system: str, log_text: str) -> dict:
    db = engine.db
    t0 = time.monotonic()
    error = None
    transcript: list[dict] = []
    art: dict = {"reply": ""}
    scope = ""
    verdict: dict | None = None
    system_ms: int | None = None
    try:
        if system == "murray":
            transcript, art, scope = await _run_murray(engine, case, label)
        else:
            transcript, art, scope = await _run_baseline(engine, case, system, label, log_text)
        system_ms = int((time.monotonic() - t0) * 1000) - sum(int(t.get("ms") or 0) for t in transcript if t.get("sleep"))
        has_memory = system in ("murray", "baseline_neon_log")
        evidence, valid = judge_mod.evidence_pack(db, art.get("reply", ""), scope=scope)
        art["valid_citations"] = valid if has_memory else []
        if case.get("criteria"):
            img = art.get("image") or {}
            picture = _png(img.get("sheet") or img.get("path")) if case["type"] == "render" else None
            verdict = await judge_mod.judge(engine.llm, case, transcript, evidence, picture, has_memory=has_memory, scope=scope)
    except Exception as exc:  # noqa: BLE001 - one failed case must not stop the run
        error = f"{type(exc).__name__}: {str(exc)[:400]}"
    scored = scoring.score_case(case, system, art, verdict) if not error else {"score": 0.0, "outcome": "error", "det": {}}
    turns = sum(1 for t in transcript if not t.get("sleep")) or 1
    cost = float(db.val("SELECT COALESCE(SUM(cost_usd),0) FROM llm_calls WHERE scope=? AND role<>'judge'", (scope,), 0.0)) if scope else 0.0
    total_ms = int((time.monotonic() - t0) * 1000)
    latency = system_ms if system_ms is not None else total_ms       # the system's answering time: no judge, no sleep
    metrics = {"outcome": scored["outcome"], "turns": turns, "calls": db.val("SELECT COUNT(*) FROM llm_calls WHERE scope=?", (scope,), 0) if scope else 0,
               "latency": "system", "total_ms": total_ms, "sleep_ms": sum(int(t.get("ms") or 0) for t in transcript if t.get("sleep"))}
    db.x("INSERT OR REPLACE INTO eval_results(eval_run_id, case_id, system, split, category, session_id, transcript_json, final_render_id, "
         "artifacts_json, det_json, judge_json, score, metrics_json, latency_ms, cost_usd, error, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
         (run_id, case["id"], system, "holdout" if case.get("holdout") else "dev", case["category"], art.get("session"),
          dumps(transcript), None, dumps(art), dumps(scored["det"]), dumps(verdict) if verdict else None, scored["score"],
          dumps(metrics), latency, cost, error, now()))
    return {"case_id": case["id"], "system": system, "score": scored["score"], "outcome": scored["outcome"], "error": error}


def results_for(db: DB, run_id: int) -> list[dict]:
    out = []
    for r in db.q("SELECT * FROM eval_results WHERE eval_run_id=?", (run_id,)):
        out.append({"case_id": r["case_id"], "system": r["system"], "score": r["score"], "det": loads(r["det_json"], {}),
                    "latency_ms": r["latency_ms"], "cost_usd": r["cost_usd"], "error": r["error"],
                    "turns": (loads(r["metrics_json"], {}) or {}).get("turns", 1)})
    return out


def fix_latency(db: DB, label: str) -> int:
    """Rows recorded before latency meant "the system's own answering time" include the judge's call and any sleep.
    Take both back out, using the logged model calls. Each row is corrected once."""
    run_id = db.val("SELECT id FROM eval_runs WHERE label=?", (label,), None)
    fixed = 0
    for r in db.q("SELECT * FROM eval_results WHERE eval_run_id=?", (run_id,)):
        metrics = loads(r["metrics_json"], {}) or {}
        if metrics.get("latency") == "system" or r["error"]:
            continue
        if r["system"] == "murray":
            scope = "s:" + (r["session_id"] or "")
        else:
            scope = db.val("SELECT scope FROM llm_calls WHERE scope LIKE ? ORDER BY id DESC LIMIT 1", (f"eval:{label}:{r['case_id']}:{r['system']}:%",), "")
        judge_ms = int(db.val("SELECT COALESCE(SUM(latency_ms),0) FROM llm_calls WHERE scope=? AND role='judge'", (scope,), 0) or 0)
        slept = any(t.get("sleep") for t in loads(r["transcript_json"], []) or [])
        sleep_ms = int(db.val("SELECT COALESCE(SUM(latency_ms),0) FROM llm_calls WHERE scope=? AND role='carbon'", (scope,), 0) or 0) if slept and r["system"] == "murray" else 0
        total = int(r["latency_ms"] or 0)
        metrics.update({"latency": "system", "total_ms": total, "sleep_ms": sleep_ms, "judge_ms": judge_ms})
        db.x("UPDATE eval_results SET latency_ms=?, metrics_json=? WHERE id=?", (max(total - judge_ms - sleep_ms, 0), dumps(metrics), r["id"]))
        fixed += 1
    return fixed


async def rejudge(engine: Engine, label: str) -> dict:
    """Judge again the rows whose evidence pack was wrong the first time: it left out the response's own
    conversation and every citation after the eighth, so the judge was told that real episodes did not exist.
    Only rows whose set of valid citations changes are sent back; the saved transcript and image are reused."""
    db = engine.db
    test_set, _ = load_test_set()
    cases = {c["id"]: c for c in test_set["cases"]}
    run_id = db.val("SELECT id FROM eval_runs WHERE label=?", (label,), None)
    fix_latency(db, label)               # before a second judge call is logged against the same conversation
    again = []
    for r in db.q("SELECT * FROM eval_results WHERE eval_run_id=? AND error IS NULL", (run_id,)):
        case = cases.get(r["case_id"])
        if r["system"] not in ("murray", "baseline_neon_log") or not case or not case.get("criteria"):
            continue
        art, transcript = loads(r["artifacts_json"], {}) or {}, loads(r["transcript_json"], []) or []
        scope = ("s:" + (r["session_id"] or "")) if r["system"] == "murray" else (
            db.val("SELECT scope FROM llm_calls WHERE scope LIKE ? ORDER BY id DESC LIMIT 1", (f"eval:{label}:{r['case_id']}:{r['system']}:%",), "") or "")
        evidence, valid = judge_mod.evidence_pack(db, art.get("reply", ""), scope=scope)
        if sorted(valid) == sorted(art.get("valid_citations") or []):
            continue
        img = art.get("image") or {}
        picture = _png(img.get("sheet") or img.get("path")) if case["type"] == "render" else None
        verdict = await judge_mod.judge(engine.llm, case, transcript, evidence, picture, has_memory=True, scope=scope)
        art["valid_citations"] = valid
        art["rejudged"] = "the first evidence pack left out episodes that this response cites"
        db.x("UPDATE eval_results SET artifacts_json=?, judge_json=? WHERE id=?", (dumps(art), dumps(verdict), r["id"]))
        again.append(f"{r['case_id']}/{r['system']}")
    card = rescore(db, label)
    return {"rejudged": again, "mean_scores": {s: v["mean_score"] for s, v in card["systems"].items()}}


def with_combined(db: DB, run_id: int, card: dict, test_set: dict) -> dict:
    """If another finished round ran the same system (same prompt hash) on different cases, add the paired statistics
    over both rounds' cases together. Rounds are named r1, r2, ...; smoke runs are ignored."""
    row = db.one("SELECT label, prompt_hash FROM eval_runs WHERE id=?", (run_id,))
    mine = results_for(db, run_id)
    my_cases = {r["case_id"] for r in mine}
    for other in db.q("SELECT id, label FROM eval_runs WHERE prompt_hash=? AND id<>? AND status='done' ORDER BY id", (row["prompt_hash"], run_id)):
        label = other["label"] or ""
        if not (label[:1] == "r" and label[1:].isdigit()):
            continue
        theirs = results_for(db, other["id"])
        if not theirs or {r["case_id"] for r in theirs} & my_cases:
            continue
        both = scoring.scorecard(test_set["cases"], mine + theirs)
        card["combined"] = {"with": label, "cases": len(my_cases | {r["case_id"] for r in theirs}),
                            "systems": {s: {"n": v["n"], "mean_score": v["mean_score"], "by_category": v["by_category"]} for s, v in both["systems"].items()},
                            "comparison": both.get("comparison", {})}
        break
    return card


def rescore(db: DB, label: str) -> dict:
    """Re-score a stored round from its artifacts with the current scorer, and rewrite its scorecard."""
    test_set, _ = load_test_set()
    cases = {c["id"]: c for c in test_set["cases"]}
    run_id = db.val("SELECT id FROM eval_runs WHERE label=?", (label,), None)
    if run_id is None:
        raise SystemExit(f"no eval run labelled {label}")
    for r in db.q("SELECT * FROM eval_results WHERE eval_run_id=?", (run_id,)):
        if r["error"] or r["case_id"] not in cases:
            continue
        scored = scoring.score_case(cases[r["case_id"]], r["system"], loads(r["artifacts_json"], {}), loads(r["judge_json"], None))
        metrics = loads(r["metrics_json"], {}) or {}
        metrics["outcome"] = scored["outcome"]
        db.x("UPDATE eval_results SET score=?, det_json=?, metrics_json=? WHERE id=?", (scored["score"], dumps(scored["det"]), dumps(metrics), r["id"]))
    card = with_combined(db, run_id, scoring.scorecard(test_set["cases"], results_for(db, run_id)), test_set)
    db.x("INSERT OR REPLACE INTO eval_scorecards(eval_run_id, json, created_at) VALUES(?,?,?)", (run_id, dumps(card), now()))
    return card


async def run(engine: Engine, label: str = "r1", systems: list[str] | None = None, case_ids: list[str] | None = None,
              concurrency: int = 3, image_tag: str = "") -> dict:
    db = engine.db
    test_set, sha = load_test_set()
    systems = systems or SYSTEMS
    if case_ids in (["base"], ["extension"]):          # a whole set by name
        case_ids = [c["id"] for c in test_set["cases"] if (c.get("set") == "extension") == (case_ids == ["extension"])]
    elif not case_ids:                                  # by default a round is the first set, as rounds 1 and 2 were
        case_ids = [c["id"] for c in test_set["cases"] if c.get("set") != "extension"]
    cases = [c for c in test_set["cases"] if c["id"] in case_ids]
    if any(c.get("set") == "extension" for c in cases):
        sha = test_set.get("extension_sha256") or sha
    row = db.one("SELECT * FROM eval_runs WHERE label=?", (label,))
    flags = {"argon_hints": engine.argon_hints, "systems": systems}
    if row is None:
        run_id = db.insert("eval_runs", label=label, status="running", image_tag=image_tag, flags_json=dumps(flags),
                           prompt_hash=prompts.prompt_hash(), test_set_sha256=sha, detector_sha256=imaging.detector_hash(),
                           started_at=now())
    else:
        run_id = row["id"]
        db.update("eval_runs", run_id, status="running")
    done = {(r["case_id"], r["system"]) for r in db.q("SELECT case_id, system FROM eval_results WHERE eval_run_id=? AND error IS NULL", (run_id,))}
    pairs = [(c, s) for c in cases for s in systems
             if (c["id"], s) not in done and (s != "baseline_neon_log" or c["type"] in LOG_TYPES)]
    log_text = baseline.raw_log(db) if "baseline_neon_log" in systems else ""
    sem = asyncio.Semaphore(max(1, concurrency))
    progress = {"total": len(pairs), "done": 0, "errors": 0}
    db.update("eval_runs", run_id, progress_json=dumps(progress))

    async def one(case: dict, system: str) -> None:
        async with sem:
            if engine.llm.breaker():            # the runaway guard is open: leave the rest for a later, resumed run
                progress["skipped"] = progress.get("skipped", 0) + 1
                return
            res = await run_pair(engine, run_id, label, case, system, log_text)
        progress["done"] += 1
        progress["errors"] += 1 if res["error"] else 0
        progress["last"] = f"{res['case_id']}/{res['system']}: {res['outcome']} ({res['score']})"
        db.update("eval_runs", run_id, progress_json=dumps(progress))
        engine.emit("canon", "eval_progress", {"label": label, **progress})

    await asyncio.gather(*(one(c, s) for c, s in pairs))
    db.update("eval_runs", run_id, status="done")              # so that a sibling round can find this one
    card = with_combined(db, run_id, scoring.scorecard(test_set["cases"], results_for(db, run_id)), test_set)
    db.x("INSERT OR REPLACE INTO eval_scorecards(eval_run_id, json, created_at) VALUES(?,?,?)", (run_id, dumps(card), now()))
    db.update("eval_runs", run_id, status="paused" if progress.get("skipped") else "done", finished_at=now())
    engine.emit("canon", "eval_done", {"label": label, "skipped": progress.get("skipped", 0)})
    return card


async def _main(argv: list[str]) -> int:
    from murray.llm import LLM
    from murray.sandbox.client import get_sandbox

    def opt(name: str, default: str | None = None) -> str | None:
        return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default

    db = DB(config.DB_PATH)
    if "--rescore" in argv:
        fixed = fix_latency(db, opt("--label", "r1"))
        card = rescore(db, opt("--label", "r1"))
        print(json.dumps({"latency_rows_corrected": fixed, "scorecard": card}, indent=1))
        return 0
    engine = Engine(db, LLM(db), get_sandbox())
    if "--rejudge" in argv:
        print(json.dumps(await rejudge(engine, opt("--label", "r1")), indent=1))
        return 0
    engine.argon_hints = "--hints" in argv
    card = await run(engine, label=opt("--label", "r1"), systems=(opt("--systems") or ",".join(SYSTEMS)).split(","),
                     case_ids=(opt("--cases").split(",") if opt("--cases") else None), concurrency=int(opt("--concurrency", "3")),
                     image_tag=opt("--tag", ""))
    print(json.dumps(card, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
