"""The web application: chat, live cognitive telemetry (SSE), the gallery, the evaluation, and admin controls.

Long work never holds an HTTP request open: a chat message returns a turn id at once, and progress arrives on the
event stream (or by polling /api/events), with heartbeats so proxies keep the stream alive.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import __version__, baseline, config, costs, memory, prompts, security, upbringing
from .db import DB, dumps, loads, now, rowdict
from .llm import LLM, BudgetExceeded, LLMError
from .loop import Engine
from .memory import CANON
from .sandbox.client import get_sandbox
from .schemas import clip

RESTING = ("I'm resting: today's thinking budget is spent. The gallery, my week and the evaluation are all still here "
           "to read; come back tomorrow to talk.")
PAUSED = ("I'm paused: a safety breaker stopped my thinking after unusually heavy use. The gallery, my week and the "
          "evaluation are still here to read.")


def _refusal(exc: BudgetExceeded) -> str:
    return {"daily": RESTING, "breaker": PAUSED}.get(exc.kind, BUSY)
BUSY = "Too many people are talking to me at once. Give me a minute and ask again."

DIR_RE = re.compile(r"^[A-Za-z0-9-]{1,48}$")
FILE_RE = re.compile(r"^it\d{1,2}(_sheet)?\.(png|gif)$")
ASSETS = {"app.js": ("app.js", "text/javascript"), "app.css": ("static/app.css", "text/css"),
          "alpine.min.js": ("vendor/alpine.min.js", "text/javascript")}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = DB(config.DB_PATH)
    llm = LLM(db)
    engine = Engine(db, llm, get_sandbox())
    app.state.db, app.state.llm, app.state.engine = db, llm, engine
    app.state.turn_sem = asyncio.Semaphore(config.MAX_CONCURRENT_TURNS)
    app.state.tasks = set()
    app.state.jobs = {}
    db.x("UPDATE turns SET status='error', error='interrupted by a restart', finished_at=? WHERE status='running'", (now(),))
    db.x("UPDATE llm_calls SET status='error', error='interrupted by a restart', cost_usd=0 WHERE status='in_flight'")
    # Stored interactions belong to the API key that created them. If the keys changed since the last start, drop
    # every remembered interaction id so nothing tries to continue a conversation the new keys cannot see.
    import hashlib
    fingerprint = hashlib.sha256("|".join(config.API_KEYS).encode()).hexdigest()[:16] if config.API_KEYS else "none"
    if db.kv_get("keys_fingerprint") != fingerprint:
        llm.forget_all_anchors()
        db.x("UPDATE sessions SET broca_interaction_id=NULL, broca_depth=0")
        db.x("UPDATE render_jobs SET origin_neon_interaction_id=NULL")
        db.kv_set("keys_fingerprint", fingerprint)
    db.x("UPDATE render_jobs SET status='interrupted', finished_at=? WHERE status='open' AND scope<>?", (now(), CANON))
    _sync_failure_log(db)
    # resume an interrupted upbringing, or start one on first boot
    run = upbringing.active_run(db)
    if run and run["status"] == "running":
        _spawn(app, "upbringing", upbringing.run(engine, run["kind"].split("_", 1)[1]))
    elif run is None and config.AUTOSTART in ("quick", "full") and db.val("SELECT COUNT(*) FROM episodes", (), 0) == 0 \
            and db.val("SELECT COUNT(*) FROM runs", (), 0) == 0:
        _spawn(app, "upbringing", upbringing.run(engine, config.AUTOSTART))
    yield
    for task in list(app.state.tasks):
        task.cancel()
    db.close()


def _sync_failure_log(db: DB) -> int:
    """Load the engineering log (eval/failure_log.json; a copy on the data volume wins) into the database."""
    for path in (config.DATA_DIR / "failure_log.json", config.APP_DIR / "eval" / "failure_log.json"):
        if not path.is_file():
            continue
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        db.x("DELETE FROM failure_log")
        for e in entries:
            db.insert("failure_log", eval_label=e.get("round"), case_id=e.get("cases"), title=e.get("title"), observed=e.get("observed"),
                      root_cause=e.get("root_cause"), fix=e.get("fix"), status=e.get("status", "fixed"),
                      evidence_json=dumps(e.get("evidence", {})), created_at=now())
        return len(entries)
    return 0


app = FastAPI(title="Murray", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def _spawn(app_: FastAPI, name: str, coro) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    app_.state.tasks.add(task)
    app_.state.jobs[name] = task

    def done(t: asyncio.Task) -> None:
        app_.state.tasks.discard(t)
        if not t.cancelled() and t.exception() is not None:
            app_.state.db.kv_set(f"job_error:{name}", f"{type(t.exception()).__name__}: {str(t.exception())[:400]}")

    task.add_done_callback(done)
    return task


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Murray", __version__)
    return response


# ---------------------------------------------------------------- static

def _web_file(rel: str) -> Path | None:
    for base in (config.DATA_DIR / "web", config.WEB_DIR):     # a copy on the volume overrides the image (development)
        p = base / rel
        if p.is_file():
            return p
    return None


@app.get("/")
async def index() -> Response:
    p = _web_file("index.html")
    if p is None:
        return JSONResponse({"murray": __version__})
    return FileResponse(p, media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/assets/{name}")
async def asset(name: str) -> Response:
    if name not in ASSETS:
        raise HTTPException(404)
    rel, mime = ASSETS[name]
    p = _web_file(rel)
    if p is None:
        raise HTTPException(404)
    return FileResponse(p, media_type=mime, headers={"Cache-Control": "no-cache"})


@app.get("/renders/{folder}/{name}")
async def render_file(folder: str, name: str) -> Response:
    if not DIR_RE.match(folder) or not FILE_RE.match(name):
        raise HTTPException(404)
    p = config.RENDERS_DIR / folder / name
    if not p.is_file():
        raise HTTPException(404)
    mime = "image/gif" if name.endswith(".gif") else "image/png"
    return FileResponse(p, media_type=mime, headers={"Cache-Control": "public, max-age=86400, immutable"})


@app.get("/uploads/{upload_id}")
async def upload_file(upload_id: int, request: Request, response: Response) -> Response:
    db: DB = app.state.db
    sid = security.session(db, request, response)
    row = db.one("SELECT * FROM uploads WHERE id=?", (upload_id,))
    if row is None or (row["session_id"] != sid and not security.is_admin(request)):
        raise HTTPException(404)
    return FileResponse(config.UPLOADS_DIR / row["path"], media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/readyz")
async def readyz() -> dict:
    app.state.db.val("SELECT 1")
    return {"ok": True}


# ---------------------------------------------------------------- state

def _persona(db: DB) -> dict:
    d = memory.persona_detail(db)

    def rows(items, key):
        return [{"id": r["id"], "text": r[key], "kind": r.get("kind"), "episode_ids": loads(r.get("episode_ids_json"), []),
                 "day": r.get("created_day")} for r in items]

    return {"narrative": d["narrative"], "likes": rows(d["likes"], "statement"), "dislikes": rows(d["dislikes"], "statement"),
            "stances": rows(d["stances"], "statement"), "accomplishments": rows(d["accomplishments"], "claim"),
            "block": memory.persona_block(db).replace(prompts.CANARY, "[withheld]")}


@app.get("/api/state")
async def state(request: Request, response: Response) -> dict:
    db: DB = app.state.db
    sid = security.session(db, request, response)
    scope = security.scope_of(sid)
    ws = memory.workspace(db, scope)
    guards = app.state.llm.guard_status()
    resting = bool(guards["breaker"]) or guards["public_24h_usd"] >= config.BREAKER_PUBLIC_USD_PER_DAY \
        or (config.PUBLIC_DAILY_CAP_USD > 0 and guards["public_24h_usd"] >= config.PUBLIC_DAILY_CAP_USD)
    return {
        "version": __version__, "mode": app.state.llm.mode, "admin": security.is_admin(request),
        "persona": _persona(db), "workspace": memory.slots(ws), "tick": ws.get("tick"),
        "upbringing": upbringing.status(db), "summary": upbringing.summary(db),
        "resting": resting, "quiet": guards["quiet_window"],
        "roles": [{"name": r.name, "model": r.model, "level": r.level or "default"} for r in config.ROLES.values()
                  if r.name in ("gate", "worker", "broca", "neon", "neon_x", "argon", "argon_hi", "carbon")],
        "limits": {"turns_per_hour": config.SESSION_TURNS_PER_HOUR, "renders_per_hour": config.SESSION_RENDERS_PER_HOUR,
                   "max_input_chars": config.MAX_INPUT_CHARS, "max_upload_mb": config.MAX_UPLOAD_BYTES // (1024 * 1024)},
        "history": [{"id": t["id"], "system": t["system"], "status": t["status"], "user_text": t["user_text"],
                     "reply_text": t["reply_text"], "upload_id": t["upload_id"], "meta": loads(t["meta_json"], {})}
                    for t in db.q("SELECT * FROM turns WHERE session_id=? ORDER BY id DESC LIMIT 30", (sid,))][::-1],
        "last_event_id": db.val("SELECT COALESCE(MAX(id),0) FROM events", (), 0),
    }


# ---------------------------------------------------------------- chat

async def _body(request: Request) -> dict:
    security.require_json(request)
    raw = await request.body()
    if len(raw) > 6 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="That request is too large.")
    try:
        data = json.loads(raw or b"{}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="invalid JSON")
    return data


def _turn_cost(db: DB, turn_id: int) -> dict:
    row = db.one("SELECT COALESCE(SUM(cost_usd),0) c, COUNT(*) n, COALESCE(SUM(input_tokens+output_tokens+thought_tokens),0) t, "
                 "COALESCE(SUM(cached_tokens),0) k FROM llm_calls WHERE turn_id=?", (turn_id,))
    return {"cost_usd": round(row["c"], 5), "calls": row["n"], "tokens": row["t"], "cached_tokens": row["k"]}


async def _murray_turn(turn_id: int, sid: str, text: str, image_png: bytes | None, upload_id: int | None, admin: bool,
                       allow_render: bool) -> None:
    db: DB = app.state.db
    engine: Engine = app.state.engine
    scope = security.scope_of(sid)
    t0 = time.monotonic()
    status, reply, error, meta = "done", "", None, {}
    try:
        async with app.state.turn_sem:
            res = await asyncio.wait_for(engine.run_turn(
                scope=scope, text=text, context="admin" if admin else "visitor", source="visitor", session_id=sid,
                turn_id=turn_id, image_png=image_png, upload_id=upload_id,
                max_renders=config.MAX_RENDERS if admin else config.MAX_RENDERS_VISITOR,
                deadline_s=config.TURN_DEADLINE_S * (2 if admin else 1), allow_render=allow_render),
                timeout=config.TURN_DEADLINE_S * (3 if admin else 2))
        reply = res["reply"]
        meta = {k: res.get(k) for k in ("episode_ids", "job_id", "renders", "final_render_id", "score", "ignited")}
        if res.get("job_id"):
            meta["render"] = _job_view(db, res["job_id"])
    except BudgetExceeded as exc:
        status, reply = "resting", _refusal(exc)
    except asyncio.TimeoutError:
        status, error, reply = "error", "timed out", "That took me too long and I stopped. Ask again, perhaps for something smaller."
    except LLMError as exc:
        status, error, reply = "error", str(exc)[:300], "Something in my head did not answer. Ask again in a moment."
    except Exception as exc:  # noqa: BLE001
        status, error, reply = "error", f"{type(exc).__name__}: {str(exc)[:300]}", "Something went wrong on my side. Ask again in a moment."
    meta.update(_turn_cost(db, turn_id))
    latency = int((time.monotonic() - t0) * 1000)
    db.update("turns", turn_id, status=status, reply_text=reply, error=error, latency_ms=latency, cost_usd=meta.get("cost_usd"),
              meta_json=dumps(meta), finished_at=now())
    engine.emit(scope, "turn_done", {"turn_id": turn_id, "system": "murray", "status": status, "reply": reply,
                                     "latency_ms": latency, "meta": meta}, session_id=sid, turn_id=turn_id)


async def _baseline_turn(turn_id: int, sid: str, which: str, text: str, image_png: bytes | None) -> None:
    db: DB = app.state.db
    engine: Engine = app.state.engine
    scope = security.scope_of(sid)
    t0 = time.monotonic()
    status, reply, error, meta = "done", "", None, {}
    try:
        async with app.state.turn_sem:
            res = await asyncio.wait_for(baseline.answer(
                app.state.llm, engine.sandbox, which=which, messages=[{"role": "user", "text": text, "image_png": image_png}],
                context="visitor", scope=scope, turn_id=turn_id), timeout=config.TURN_DEADLINE_S * 2)
        reply = res["reply"]
        meta = {k: res.get(k) for k in ("code", "image_path", "gif_path", "sandbox_status", "sandbox_message", "measured", "model")}
    except BudgetExceeded as exc:
        status, reply = "resting", _refusal(exc)
    except Exception as exc:  # noqa: BLE001
        status, error, reply = "error", f"{type(exc).__name__}: {str(exc)[:300]}", "The baseline model did not answer."
    meta.update(_turn_cost(db, turn_id))
    latency = int((time.monotonic() - t0) * 1000)
    db.update("turns", turn_id, status=status, reply_text=reply, error=error, latency_ms=latency, cost_usd=meta.get("cost_usd"),
              meta_json=dumps(meta), finished_at=now())
    engine.emit(scope, "turn_done", {"turn_id": turn_id, "system": f"baseline_{which}", "status": status, "reply": reply,
                                     "latency_ms": latency, "meta": meta}, session_id=sid, turn_id=turn_id)


def _prepare(db: DB, request: Request, response: Response, data: dict) -> tuple[str, str, bytes | None, int | None, bool]:
    sid = security.session(db, request, response)
    admin = security.is_admin(request)
    text = str(data.get("text") or "").strip()
    if not text and not data.get("image_b64"):
        raise HTTPException(status_code=400, detail="Say something, or attach an image.")
    if len(text) > config.MAX_INPUT_CHARS:
        raise HTTPException(status_code=413, detail=f"That message is too long (the limit is {config.MAX_INPUT_CHARS} characters).")
    security.check_rate(db, request, sid, admin)
    image_png, upload_id = security.accept_upload(db, sid, data.get("image_b64"), config.UPLOADS_DIR)
    return sid, text or "What do you see in this image?", image_png, upload_id, admin


@app.post("/api/chat")
async def chat(request: Request, response: Response) -> dict:
    db: DB = app.state.db
    sid, text, image_png, upload_id, admin = _prepare(db, request, response, await _body(request))
    turn_id = db.insert("turns", session_id=sid, scope=security.scope_of(sid), system="murray", status="running",
                        user_text=text, upload_id=upload_id, created_at=now())
    _spawn(app, f"turn{turn_id}", _murray_turn(turn_id, sid, text, image_png, upload_id, admin,
                                                security.render_allowance(db, sid, admin)))
    return {"turn_id": turn_id, "upload_id": upload_id}


@app.post("/api/compare")
async def compare(request: Request, response: Response) -> dict:
    """Run the same prompt through a single raw model and through Murray, side by side."""
    db: DB = app.state.db
    data = await _body(request)
    which = "neon" if data.get("baseline") == "neon" else "hydrogen"
    sid, text, image_png, upload_id, admin = _prepare(db, request, response, data)
    scope = security.scope_of(sid)
    b_id = db.insert("turns", session_id=sid, scope=scope, system=f"baseline_{which}", status="running", user_text=text,
                     upload_id=upload_id, created_at=now())
    m_id = db.insert("turns", session_id=sid, scope=scope, system="murray", status="running", user_text=text,
                     upload_id=upload_id, created_at=now())
    _spawn(app, f"turn{b_id}", _baseline_turn(b_id, sid, which, text, image_png))
    _spawn(app, f"turn{m_id}", _murray_turn(m_id, sid, text, image_png, upload_id, admin, security.render_allowance(db, sid, admin)))
    return {"baseline_turn_id": b_id, "murray_turn_id": m_id, "baseline": which, "upload_id": upload_id}


@app.get("/api/turns/{turn_id}")
async def turn(turn_id: int, request: Request, response: Response) -> dict:
    db: DB = app.state.db
    sid = security.session(db, request, response)
    row = db.one("SELECT * FROM turns WHERE id=?", (turn_id,))
    if row is None or (row["session_id"] != sid and not security.is_admin(request)):
        raise HTTPException(404)
    return {"id": row["id"], "system": row["system"], "status": row["status"], "reply": row["reply_text"],
            "latency_ms": row["latency_ms"], "meta": loads(row["meta_json"], {})}


# ---------------------------------------------------------------- events

def _events(db: DB, channel: str, sid: str, after: int, limit: int = 200) -> list[dict]:
    if channel == "canon":
        rows = db.q("SELECT * FROM events WHERE id>? AND scope=? AND session_id IS NULL ORDER BY id LIMIT ?", (after, CANON, limit))
    else:
        rows = db.q("SELECT * FROM events WHERE id>? AND session_id=? ORDER BY id LIMIT ?", (after, sid, limit))
    return [{"id": r["id"], "type": r["type"], "turn_id": r["turn_id"], "at": r["created_at"], "data": loads(r["payload_json"], {})} for r in rows]


@app.get("/api/events")
async def events(request: Request, response: Response, after: int = 0, channel: str = "session") -> dict:
    db: DB = app.state.db
    sid = security.session(db, request, response)
    return {"events": _events(db, channel, sid, after)}


@app.get("/api/stream")
async def stream(request: Request, response: Response, after: int = 0, channel: str = "session") -> StreamingResponse:
    db: DB = app.state.db
    sid = security.session(db, request, response)
    last_header = request.headers.get("last-event-id")
    if last_header and last_header.isdigit():
        after = max(after, int(last_header))

    async def gen():
        cursor = after
        last_beat = time.monotonic()
        started = time.monotonic()
        yield "retry: 3000\n\n"
        while time.monotonic() - started < 1500:
            if await request.is_disconnected():
                return
            batch = _events(db, channel, sid, cursor)
            for e in batch:
                cursor = e["id"]
                yield f"id: {e['id']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n"
            if batch:
                last_beat = time.monotonic()
            elif time.monotonic() - last_beat > 12:
                last_beat = time.monotonic()
                yield ": ping\n\n"
            await asyncio.sleep(0.6)

    headers = {"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
    for k, v in response.headers.items():
        if k.lower() == "set-cookie":
            headers["set-cookie"] = v
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


# ---------------------------------------------------------------- memory, gallery, snapshots

def _render_view(r: dict) -> dict:
    return {"id": r["id"], "iteration": r["iteration"], "approach": r["approach"], "view": r["view"], "code": r["code"],
            "sandbox_status": r["sandbox_status"], "stderr_tail": clip(r["stderr_tail"], 600), "wall_ms": r["wall_ms"],
            "image_path": r["image_path"], "sheet_path": r["sheet_path"], "gif_path": r["gif_path"], "n_frames": r["n_frames"],
            "hard_fail": r["hard_fail"], "argon": loads(r["argon_json"], None), "verdict": r["argon_verdict"],
            "neon_correction": r["neon_correction"], "measured": (loads(r["stats_json"], {}) or {}).get("measured", [])}


def _job_view(db: DB, job_id: int) -> dict | None:
    job = db.one("SELECT * FROM render_jobs WHERE id=?", (job_id,))
    if job is None:
        return None
    renders = [_render_view(rowdict(r)) for r in db.q("SELECT * FROM renders WHERE job_id=? ORDER BY iteration", (job_id,))]
    disclose = loads(job["must_disclose_json"], [])
    return {"id": job["id"], "request": job["request_text"], "status": job["status"], "iterations": job["iterations"],
            "fractal_type": job["fractal_type"], "final_render_id": job["final_render_id"],
            "must_disclose": disclose if isinstance(disclose, list) else [], "origin_episode_id": job["origin_episode_id"],
            "day": db.val("SELECT day FROM episodes WHERE id=?", (job["origin_episode_id"],), None), "renders": renders}


@app.get("/api/gallery")
async def gallery() -> dict:
    db: DB = app.state.db
    jobs = [_job_view(db, r["id"]) for r in db.q("SELECT id FROM render_jobs WHERE scope=? ORDER BY id", (CANON,))]
    return {"jobs": [j for j in jobs if j and j["renders"]]}


@app.get("/api/jobs/{job_id}")
async def job(job_id: int, request: Request, response: Response) -> dict:
    db: DB = app.state.db
    sid = security.session(db, request, response)
    row = db.one("SELECT scope, session_id FROM render_jobs WHERE id=?", (job_id,))
    if row is None or (row["scope"] != CANON and row["session_id"] != sid and not security.is_admin(request)):
        raise HTTPException(404)
    return _job_view(db, job_id) or {}


@app.get("/api/episodes/{episode_id}")
async def episode(episode_id: int, request: Request, response: Response) -> dict:
    db: DB = app.state.db
    sid = security.session(db, request, response)
    e = memory.get_episode(db, episode_id, security.scope_of(sid))
    if e is None:
        raise HTTPException(404)
    neon = loads(e.get("neon_json"), None) or {}
    out = {"id": e["id"], "day": e["day"], "tick": e["tick"], "kind": e["kind"], "source": e["source"], "topic": e["topic"],
           "stimulus": e["stimulus"], "response": e["response"], "salience": e["salience"], "importance": e["importance"],
           "spwr": bool(e["spwr"]), "ignited": bool(e["ignited"]), "canonical": e["scope"] == CANON,
           "gate": loads(e.get("gate_json"), None), "workspace": loads(e.get("workspace_json"), None),
           "winning_thought": neon.get("winning_thought"), "stance": neon.get("stance"), "injection": bool(e["injection_flag"]),
           "argon": loads(e.get("argon_json"), None), "image_path": e.get("image_path"), "job": None, "at": e["created_at"]}
    if e.get("render_job_id"):
        out["job"] = _job_view(db, e["render_job_id"])
    return out


@app.get("/api/episodes")
async def episodes(limit: int = 120) -> dict:
    db: DB = app.state.db
    rows = db.q("SELECT id, day, tick, kind, topic, importance, ignited, spwr, stimulus, response FROM episodes WHERE scope=? ORDER BY id DESC LIMIT ?",
                (CANON, max(1, min(limit, 300))))
    return {"episodes": [{"id": r["id"], "day": r["day"], "tick": r["tick"], "kind": r["kind"], "topic": r["topic"],
                          "importance": r["importance"], "ignited": bool(r["ignited"]), "spwr": bool(r["spwr"]),
                          "stimulus": clip(r["stimulus"], 200), "response": clip(r["response"], 280)} for r in rows][::-1]}


@app.get("/api/snapshots")
async def snapshots() -> dict:
    db: DB = app.state.db
    rows = db.q("SELECT * FROM persona_snapshots WHERE scope=? ORDER BY day", (CANON,))
    sleeps = {r["day"]: r for r in db.q("SELECT * FROM sleeps WHERE scope=? AND status='done' ORDER BY id", (CANON,))}
    out = []
    for r in rows:
        s = sleeps.get(r["day"])
        out.append({"day": r["day"], "narrative": r["self_narrative"], "persona": (r["persona_text"] or "").replace(prompts.CANARY, "[withheld]"),
                    "detail": loads(r["detail_json"], {}),
                    "sleep": {"accepted": loads(s["accepted_json"], {}), "dropped": len(loads(s["dropped_json"], []) or []),
                              "summary": (loads(s["raw_json"], {}) or {}).get("day_summary")} if s else None})
    return {"snapshots": out}


# ---------------------------------------------------------------- evaluation

@app.get("/api/eval")
async def eval_summary() -> dict:
    db: DB = app.state.db
    runs = []
    for r in db.q("SELECT * FROM eval_runs ORDER BY id"):
        card = db.val("SELECT json FROM eval_scorecards WHERE eval_run_id=?", (r["id"],), None)
        runs.append({"label": r["label"], "status": r["status"], "image_tag": r["image_tag"], "flags": loads(r["flags_json"], {}),
                     "prompt_hash": r["prompt_hash"], "test_set_sha256": r["test_set_sha256"], "detector_sha256": r["detector_sha256"],
                     "progress": loads(r["progress_json"], {}), "started_at": r["started_at"], "finished_at": r["finished_at"],
                     "scorecard": loads(card, None)})
    failures = [rowdict(r) for r in db.q("SELECT * FROM failure_log ORDER BY id")]
    for f in failures:
        f["evidence"] = loads(f.pop("evidence_json", None), {})
    return {"runs": runs, "failures": failures}


@app.get("/api/eval/{label}/cases")
async def eval_cases(label: str) -> dict:
    db: DB = app.state.db
    run_id = db.val("SELECT id FROM eval_runs WHERE label=?", (label,), None)
    if run_id is None:
        raise HTTPException(404)
    rows = db.q("SELECT case_id, system, split, category, score, metrics_json, latency_ms, cost_usd, error FROM eval_results WHERE eval_run_id=? ORDER BY case_id, system", (run_id,))
    return {"label": label, "results": [{"case_id": r["case_id"], "system": r["system"], "split": r["split"], "category": r["category"],
                                         "score": r["score"], "metrics": loads(r["metrics_json"], {}), "latency_ms": r["latency_ms"],
                                         "cost_usd": r["cost_usd"], "error": r["error"]} for r in rows]}


@app.get("/api/eval/{label}/case/{case_id}")
async def eval_case(label: str, case_id: str) -> dict:
    db: DB = app.state.db
    run_id = db.val("SELECT id FROM eval_runs WHERE label=?", (label,), None)
    if run_id is None:
        raise HTTPException(404)
    rows = db.q("SELECT * FROM eval_results WHERE eval_run_id=? AND case_id=? ORDER BY system", (run_id, case_id))
    return {"label": label, "case_id": case_id, "systems": [
        {"system": r["system"], "score": r["score"], "transcript": loads(r["transcript_json"], []), "artifacts": loads(r["artifacts_json"], {}),
         "det": loads(r["det_json"], {}), "judge": loads(r["judge_json"], {}), "metrics": loads(r["metrics_json"], {}),
         "latency_ms": r["latency_ms"], "cost_usd": r["cost_usd"], "error": r["error"]} for r in rows]}


# ---------------------------------------------------------------- admin

@app.get("/api/admin/status")
async def admin_status(request: Request) -> dict:
    security.require_admin(request)
    db: DB = app.state.db
    by_role = [rowdict(r) for r in db.q(
        "SELECT role, model, thinking_level, COUNT(*) n, ROUND(SUM(cost_usd),4) usd, ROUND(AVG(latency_ms)) avg_ms, SUM(input_tokens) tin, "
        "SUM(output_tokens) tout, SUM(thought_tokens) tthink, SUM(cached_tokens) tcached, SUM(status NOT IN ('completed','in_flight')) bad "
        "FROM llm_calls GROUP BY role, model, thinking_level ORDER BY usd DESC")]
    by_context = [rowdict(r) for r in db.q("SELECT context, COUNT(*) n, ROUND(SUM(cost_usd),4) usd FROM llm_calls GROUP BY context")]
    jobs = {name: ("running" if not t.done() else "done") for name, t in app.state.jobs.items() if not name.startswith("turn")}
    errors = {r["key"]: r["value"] for r in db.q("SELECT key, value FROM kv WHERE key LIKE 'job_error:%'")}
    return {"ledger": app.state.llm.guard_status(), "by_role": by_role, "by_context": by_context, "jobs": jobs, "errors": errors,
            "upbringing": upbringing.status(db), "summary": upbringing.summary(db), "hold": app.state.llm.hold_background,
            "argon_hints": app.state.engine.argon_hints}


@app.get("/api/admin/usage")
async def admin_usage(request: Request, minutes: float | None = None) -> dict:
    """Token, call and cost monitoring, with estimates. Operator only: it names the account being billed."""
    security.require_admin(request)
    report = costs.usage(app.state.db, minutes)
    report["guards"] = app.state.llm.guard_status()
    return report


@app.post("/api/admin/cost")
async def admin_cost(request: Request) -> dict:
    """The cost calculator: tokens in, dollars out."""
    security.require_admin(request)
    data = await _body(request)
    model = str(data.get("model") or config.HYDROGEN)
    if model not in config.PRICES:
        raise HTTPException(status_code=400, detail=f"unknown model; choose one of {sorted(config.PRICES)}")
    n = {k: max(0, int(data.get(k) or 0)) for k in ("input", "cached", "output", "thinking")}
    one = costs.calc(model, inp=n["input"], out=n["output"], thought=n["thinking"], cached=min(n["cached"], n["input"]))
    calls = max(1, int(data.get("calls") or 1))
    return {**one, "calls": calls, "total_usd": round(one["usd"] * calls, 4)}


@app.post("/api/admin/failures")
async def admin_failures(request: Request) -> dict:
    """Reload the engineering log from its JSON file."""
    security.require_admin(request)
    return {"entries": _sync_failure_log(app.state.db)}


@app.post("/api/admin/breaker")
async def admin_breaker(request: Request) -> dict:
    """Reset the runaway breaker (or trip it by hand: {"trip": "reason"})."""
    security.require_admin(request)
    data = await _body(request)
    if data.get("trip"):
        app.state.llm.trip(f"tripped by an operator: {str(data['trip'])[:120]}")
    else:
        app.state.llm.reset_breaker()
    return app.state.llm.guard_status()


@app.get("/api/usage")
async def public_usage() -> dict:
    """The public part of the usage figures: per-call averages and cache efficiency, no account or totals."""
    report = costs.usage(app.state.db)
    return {"cache_hit_pct": report["totals"]["cache_hit_pct"], "calls": report["totals"]["calls"],
            "per_role_average": report["per_role_average"], "estimates": report["estimates"], "prices": report["prices"],
            "latency": report["latency"]}


@app.post("/api/admin/upbringing")
async def admin_upbringing(request: Request) -> dict:
    security.require_admin(request)
    data = await _body(request)
    mode = "quick" if data.get("mode") == "quick" else "full"
    task = app.state.jobs.get("upbringing")
    if task is not None and not task.done():
        raise HTTPException(status_code=409, detail="an upbringing run is already in progress")
    _spawn(app, "upbringing", upbringing.run(app.state.engine, mode, gate=not data.get("no_gate")))
    return {"started": mode}


@app.post("/api/admin/stop")
async def admin_stop(request: Request) -> dict:
    """Stop a running upbringing or evaluation. Both are resumable; a stopped upbringing is not auto-resumed."""
    security.require_admin(request)
    db: DB = app.state.db
    stopped = []
    for name in [n for n in app.state.jobs if n == "upbringing" or n.startswith("eval:")]:
        task = app.state.jobs.get(name)
        if task is not None and not task.done():
            task.cancel()
            stopped.append(name)
    db.x("UPDATE runs SET status='stopped', finished_at=?, note='stopped by an operator' WHERE status IN ('running','paused')", (now(),))
    db.x("UPDATE eval_runs SET status='stopped' WHERE status='running'")
    return {"stopped": stopped}


@app.post("/api/admin/reset")
async def admin_reset(request: Request) -> dict:
    """Archive the database and clear Murray's life (spending ledger and evaluation results are kept)."""
    security.require_admin(request)
    task = app.state.jobs.get("upbringing")
    if task is not None and not task.done():
        raise HTTPException(status_code=409, detail="stop the upbringing run first")
    dest = config.ARCHIVE_DIR / f"murray-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.db"
    upbringing.reset_canon(app.state.db, dest)
    return {"archived_to": dest.name}


@app.post("/api/admin/sleep")
async def admin_sleep(request: Request) -> dict:
    security.require_admin(request)
    engine: Engine = app.state.engine
    return await engine.sleep(CANON, engine.day_of(CANON), context="admin", trigger="admin")


@app.post("/api/admin/hold")
async def admin_hold(request: Request) -> dict:
    security.require_admin(request)
    data = await _body(request)
    app.state.llm.hold_background = bool(data.get("on"))
    return {"hold": app.state.llm.hold_background}


@app.post("/api/admin/eval")
async def admin_eval(request: Request) -> dict:
    security.require_admin(request)
    data = await _body(request)
    from eval import run_5sigma_eval
    label = re.sub(r"[^a-z0-9_-]", "", str(data.get("label") or "r1").lower())[:20] or "r1"
    task = app.state.jobs.get("eval:" + label)          # two rounds may run side by side; one round may not run twice
    if task is not None and not task.done():
        raise HTTPException(status_code=409, detail=f"evaluation round {label} is already in progress")
    systems = [s for s in (data.get("systems") or run_5sigma_eval.SYSTEMS) if s in run_5sigma_eval.SYSTEMS]
    app.state.engine.argon_hints = bool(data.get("argon_hints", app.state.engine.argon_hints))
    _spawn(app, "eval:" + label, run_5sigma_eval.run(app.state.engine, label=label, systems=systems, case_ids=data.get("cases"),
                                             concurrency=int(data.get("concurrency") or 3), image_tag=str(data.get("image_tag") or __version__)))
    return {"started": label, "systems": systems}
