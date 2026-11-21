"""The cognitive loop: one tick per stimulus.

    stimulus -> Hydrogen (salience gate) -> memory retrieval -> ignition? -> Neon (Global Workspace)
             -> delegation: Hydrogen worker writes code -> sandbox -> Argon inspects -> result re-enters as a stimulus
             -> Hydrogen (Broca) speaks -> episode written;  Carbon consolidates during sleep.

One driver per scope holds that scope's lock and is the only writer of its workspace and episodes. Render
iterations only call models and the sandbox and write their own rows, so they can run beside conscious ticks.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config, imaging, memory, prompts
from .db import DB, dumps, loads, now, rowdict
from .llm import LLM, LLMResult
from .memory import CANON
from .nodes import broca, dmn, gw, salience, vision, worker
from .schemas import clip


@dataclass
class Stimulus:
    text: str
    source: str = "visitor"            # visitor | tutor | owner | eval | render_result | dmn_insight
    image_png: bytes | None = None     # an uploaded image, already normalised
    upload_id: int | None = None
    topic: str = ""
    result: dict | None = None         # set for render_result
    external: bool = True


@dataclass
class TickOutcome:
    episode_id: int
    tick: int
    reply: str | None
    delegation: dict | None
    ignited: bool
    score: int
    gate: dict
    neon: dict | None
    retrieved_ids: list[int]
    job_id: int | None = None
    final_render_id: int | None = None
    route: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _step(model: str, role: str, res: LLMResult | None) -> dict:
    out = {"model": model, "role": role}
    if res is not None:
        out.update({"level": res.level, "ms": res.latency_ms, "cost_usd": round(res.cost_usd, 5),
                    "tokens": res.input_tokens + res.output_tokens + res.thought_tokens, "cached": res.cached_tokens})
    return out


class Engine:
    def __init__(self, db: DB, llm: LLM, sandbox, data_dir: Path | None = None):
        self.db = db
        self.llm = llm
        self.sandbox = sandbox
        self.data_dir = Path(data_dir or config.DATA_DIR)
        self.renders_dir = self.data_dir / "renders"
        self.locks: dict[str, asyncio.Lock] = {}
        self.render_sem = asyncio.Semaphore(config.MAX_CONCURRENT_RENDERS)
        self.argon_hints = False        # a seam: when True the critic is also given the image measurements
        if self.db.val("SELECT COUNT(*) FROM persona_snapshots WHERE scope=? AND day=0", (CANON,), 0) == 0:
            memory.snapshot(self.db, 0)

    # ------------------------------------------------------------ plumbing

    def lock(self, scope: str) -> asyncio.Lock:
        return self.locks.setdefault(scope, asyncio.Lock())

    def emit(self, scope: str, kind: str, payload: dict, session_id: str | None = None, turn_id: int | None = None) -> None:
        self.db.insert("events", scope=scope, session_id=session_id, turn_id=turn_id, type=kind, payload_json=dumps(payload), created_at=now())

    def day_of(self, scope: str) -> int:
        if scope == CANON:
            return int(self.db.kv_get("canon_day", "1") or 1)
        return 1 + int(self.db.val("SELECT COUNT(*) FROM sleeps WHERE scope=? AND status='done'", (scope,), 0))

    def _allowed_citations(self, retrieved: list[int], scope: str) -> set[int]:
        ids = set(retrieved)
        for p in memory.active_preferences(self.db, scope, limit=40) + memory.accomplishments(self.db, scope, limit=40):
            ids.update(int(i) for i in loads(p.get("episode_ids_json"), []) or [])
        ids.update(r["id"] for r in self.db.q("SELECT id FROM episodes WHERE scope=? ORDER BY id DESC LIMIT 12", (scope,)))
        return ids

    # ------------------------------------------------------------ one tick

    async def tick(self, scope: str, stim: Stimulus, *, context: str, day: int | None = None, session_id: str | None = None,
                   turn_id: int | None = None, run_id: int | None = None, max_renders: int = config.MAX_RENDERS,
                   time_left: float | None = None, allow_render: bool = True, contemplate: bool = False) -> TickOutcome:
        db = self.db
        ctx = {"context": context, "scope": scope, "turn_id": turn_id, "run_id": run_id}
        ev = {"session_id": session_id, "turn_id": turn_id}
        day = day or self.day_of(scope)
        persona = memory.persona_block(db)
        ws = memory.workspace(db, scope)
        tick_no = int(ws.get("tick") or 0) + 1
        prefs = memory.active_preferences(db, scope)
        route: list[dict] = []
        notes: list[str] = []
        result = stim.result or {}
        is_result = stim.source == "render_result"
        job_id = result.get("job_id") if is_result else None
        self.emit(scope, "tick", {"tick": tick_no, "day": day, "source": stim.source, "text": clip(stim.text, 400),
                                  "has_image": stim.image_png is not None}, **ev)

        # 1-2. salience gate
        security = (not is_result) and salience.looks_like_injection(stim.text)
        bad_result = False
        if is_result:
            bad_result = bool(result.get("hard_fail")) or result.get("verdict") in ("fix", "fail") or not result.get("matches_request", True) or result.get("sandbox_status") != "ok"
            gate = {"reason": "the result of a render Murray started", "kind": "other", "novelty": 2, "goal_conflict": 0,
                    "visual_defect": 9 if bad_result else 3, "affect_philosophy": 0, "goal_relation": "continues",
                    "injection_suspected": False, "challenges_stance": False, "wants_render": False, "recall_cues": []}
            score = 8 if bad_result else 4
        elif stim.source == "dmn_insight":
            gate = {"reason": "an insight returned from contemplation", "kind": "other", "novelty": 6, "goal_conflict": 2,
                    "visual_defect": 0, "affect_philosophy": 8, "goal_relation": "continues", "injection_suspected": False,
                    "challenges_stance": False, "wants_render": False, "recall_cues": []}
            score = 8
        else:
            gate, gres = await salience.gate(self.llm, persona, active_goal=ws.get("active_goal") or "",
                                             stances=[p["statement"] for p in prefs], stimulus=stim.text,
                                             source=stim.source, has_image=stim.image_png is not None, **ctx)
            route.append(_step("Hydrogen", "salience gate", gres))
            if security:
                gate["injection_suspected"] = True
            score = salience.score_of(gate)

        # 3. an uploaded image goes to the visual cortex first
        argon_up = None
        if stim.image_png is not None and not is_result:
            argon_up, ares = await vision.inspect_upload(self.llm, persona, stim.image_png, question=stim.text, **ctx)
            route.append(_step("Argon", "visual cortex (upload)", ares))
            sev = max([d["severity"] for d in argon_up["defects"]] or [0])
            score = max(score, min(10, 4 + sev), config.IGNITE)
            if argon_up["instruction_like_text"]:
                security = True
            self.emit(scope, "argon", {"upload": True, **argon_up}, **ev)
        security = security or bool(gate.get("injection_suspected"))
        if security:
            score = max(score, 8)
        self.emit(scope, "gate", {"score": score, "kind": gate.get("kind"), "reason": gate.get("reason"),
                                  "components": {k: gate.get(k) for k in ("novelty", "goal_conflict", "visual_defect", "affect_philosophy", "difficulty")},
                                  "asks_experience": bool(gate.get("asks_experience")),
                                  "injection_suspected": security, "challenges_stance": bool(gate.get("challenges_stance")),
                                  "wants_render": bool(gate.get("wants_render"))}, **ev)

        # 4. memory
        query_text = stim.text
        if is_result and job_id:
            query_text = db.val("SELECT request_text FROM render_jobs WHERE id=?", (job_id,), "") or stim.text
        memories = memory.search(db, scope, gate.get("recall_cues") or [], query_text)
        facts = memory.live_facts(db, scope)
        recent = memory.recent(db, scope, 3)
        retrieved_ids = [e["id"] for e in memories]
        allowed = self._allowed_citations(retrieved_ids, scope)
        self.emit(scope, "retrieval", {"episode_ids": retrieved_ids, "facts": [f"{f['key']} = {f['value']}" for f in facts]}, **ev)

        # 5. ignition
        threshold = config.IGNITE
        refractory = tick_no <= int(ws.get("refractory_until") or 0)
        # The refractory period keeps attention from thrashing between unrelated remarks. It must never swallow work
        # that was asked for: in the first upbringing a render task arrived one tick after an ignition, scored 6
        # against the raised threshold of 8, and was answered from the armchair with no image (an attentional blink).
        # A challenge to a stance is exempt for the same reason: holding or conceding it is the executive's call.
        exempt = bool(gate.get("wants_render") or gate.get("challenges_stance") or gate.get("asks_experience"))
        if refractory and stim.external and gate.get("goal_relation") != "continues" and not exempt:
            threshold += config.REFRACTORY_BOOST
        ignite = security or score >= threshold or contemplate
        if is_result and not bad_result:
            ignite = False                      # a passing render is a reflex: no arbitration needed
        self.emit(scope, "ignition", {"ignited": ignite, "score": score, "threshold": threshold, "refractory": refractory}, **ev)

        neon: dict | None = None
        delegation: dict | None = None
        reply: str | None = None
        must_disclose: list[str] = []
        final_render_id: int | None = None
        new_ws = {"tick": tick_no}
        importance = score

        if not ignite:
            if is_result:
                # 6a. reflex: the render passed. Close the job and present it.
                final_render_id = result.get("render_id")
                db.update("render_jobs", job_id, status="pass", final_render_id=final_render_id, must_disclose_json="[]", finished_at=now())
                new_ws.update({"current_conflict_or_defect": "", "visual_spatial_state": clip((result.get("argon") or {}).get("observations"), 200),
                               "plan_step": "present the finished render"})
                argon = result.get("argon") or {}
                thought = (f"The render passed inspection on attempt {result.get('iteration')}. What it shows: {argon.get('observations', '')} "
                           f"{argon.get('aesthetic_note', '')} Settings actually used: {result.get('view', '')}.")
                if int(result.get("iteration") or 1) >= 2:
                    thought += " An earlier attempt had a defect that this one fixed: " + clip(result.get("previous_defects"), 200)
                    importance = max(importance, config.SPWR)
                # what the executive decided when it took the task on (for instance, that it would not use a
                # method it was asked to use, and why): the person is owed that, not only a description of the image
                decided = loads(db.val("SELECT e.neon_json FROM render_jobs j JOIN episodes e ON e.id=j.origin_episode_id WHERE j.id=?", (job_id,), None), {}) or {}
                if decided.get("winning_thought"):
                    thought = "What I decided when I took this on: " + clip(decided["winning_thought"], 700) + "\n\nHow it came out: " + thought
                origin = db.val("SELECT origin_neon_interaction_id FROM render_jobs WHERE id=?", (job_id,), None)
                reply, bres, bnotes = await broca.speak(self.llm, persona, winning_thought=thought, must_disclose=[],
                                                        allowed_ids=allowed, previous_interaction_id=origin, **ctx)
                route.append(_step("Hydrogen", "Broca", bres))
                notes += bnotes
            else:
                # 6b. routine chatter: answered locally
                sess = rowdict(db.one("SELECT * FROM sessions WHERE id=?", (session_id,))) if session_id else {}
                prev = sess.get("broca_interaction_id") if int(sess.get("broca_depth") or 0) < config.BROCA_CHAIN_DEPTH else None
                reply, bres, bnotes = await broca.speak_local(self.llm, persona, stimulus=stim.text, source=_who(stim.source),
                                                              memories=memories, facts=facts, allowed_ids=allowed,
                                                              previous_interaction_id=prev, **ctx)
                route.append(_step("Hydrogen", "Broca (local)", bres))
                notes += bnotes
                if session_id:
                    db.update("sessions", session_id, broca_interaction_id=bres.interaction_id,
                              broca_depth=(int(sess.get("broca_depth") or 0) + 1) if prev else 1)
        else:
            # 7. the Global Workspace ignites
            # xhigh is for the moments when the executive's own reasoning is the only safeguard: a suspected
            # injection, a stance under pressure, a paradox. A render request is not one of them (whatever Neon
            # decides there, the image is still rendered and looked at), and no turn gets it that cannot afford it.
            strong = score >= 9 or int(gate.get("goal_conflict") or 0) >= 8

            def affordable(seconds: int) -> bool:
                return time_left is None or time_left >= seconds

            xhigh = (security and affordable(config.XHIGH_MIN_BUDGET_SECURITY_S)) or (
                strong and gate.get("kind") != "task_render" and affordable(config.XHIGH_MIN_BUDGET_S))
            critic_report = ""
            render_state = ""
            attempts_left = 0
            if is_result:
                critic_report = vision.report_text(result.get("argon") or {}, result.get("hard_fail"), result.get("sandbox_message", ""))
                attempts_left = max_renders - int(result.get("iteration") or 1)
                stop = result.get("repeat") or (time_left is not None and time_left < 60)
                if attempts_left <= 0 or stop:
                    attempts_left = 0
                    render_state = (f"Job {job_id}: attempt {result.get('iteration')} of {max_renders} is finished and no further attempt is possible. "
                                    "delegation must be none. Decide what to tell the person and name every remaining defect.")
                else:
                    render_state = f"Job {job_id}: attempt {result.get('iteration')} of {max_renders} is finished; {attempts_left} attempt(s) remain."
            elif argon_up is not None:
                critic_report = vision.report_text(argon_up)
            if not allow_render and not is_result:
                render_state = "No render can be started in this conversation right now (the render allowance is used up). delegation must be none; say so if a render was asked for."
            neon, nres = await gw.arbitrate(self.llm, persona, ws=ws, stimulus=stim.text, source=stim.source, gate=gate,
                                            score=score, memories=memories, prefs=prefs, facts=facts, recent=recent,
                                            critic_report=critic_report, render_state=render_state, xhigh=xhigh, **ctx)
            route.append(_step("Neon", "Global Workspace" + (" (xhigh)" if xhigh else ""), nres))
            if neon.get("dropped_citations"):
                notes.append(f"dropped {len(neon['dropped_citations'])} citation(s) of episodes that were not retrieved")
            importance = max(score, neon["importance"])
            streak = 0
            if neon["status"] == "stuck" and neon["conflict_key"]:
                streak = (int(ws.get("stuck_streak") or 0) + 1) if neon["conflict_key"] == (ws.get("conflict_key") or "") else 1
            new_ws.update({**neon["slots"], "conflict_key": neon["conflict_key"], "stuck_streak": streak,
                           "refractory_until": tick_no + (2 if xhigh else 1)})
            thought = neon["winning_thought"]

            # awake default-mode contemplation after a 3-tick deadlock on a paradox
            deadlocked = streak >= config.STUCK_STREAK and int(gate.get("affect_philosophy") or 0) >= 6
            if (deadlocked or contemplate) and not is_result:
                insight, cres = await dmn.contemplate(self.llm, persona, question=stim.text, ws={**ws, **neon["slots"]}, memories=memories, **ctx)
                route.append(_step("Carbon", "DMN deep contemplation", cres))
                thought = (f"{insight.get('insight', '')} What stays open: {insight.get('what_stays_open', '')}").strip()
                new_ws["stuck_streak"] = 0
                importance = max(importance, 8)
                self.emit(scope, "contemplation", insight, **ev)
                notes.append("deep contemplation (deadlock)" if deadlocked else "deep contemplation (scheduled)")

            self.emit(scope, "neon", {"slots": neon["slots"], "winning_thought": thought, "stance": neon.get("stance"),
                                      "injection_verdict": neon.get("injection_verdict"), "delegation": neon["delegation"],
                                      "status": neon["status"], "runner_up": neon.get("runner_up"), "level": "xhigh" if xhigh else "high",
                                      "cited_episode_ids": neon["cited_episode_ids"]}, **ev)

            # 8. delegation
            if neon["delegation"] == "render_new" and not is_result and allow_render:
                # canonical renders are public; a session's renders live under an unguessable directory
                prefix = "job" if scope == CANON else "x" + secrets.token_hex(8) + "-"
                job_id = db.insert("render_jobs", scope=scope, session_id=session_id, request_text=stim.text, status="open",
                                   origin_neon_interaction_id=None if security else nres.interaction_id, created_at=now())
                db.update("render_jobs", job_id, dir=f"{prefix}{job_id}")
                delegation = {"mode": "new", "job_id": job_id, "brief": neon["delegation_brief"] or stim.text,
                              "chain_from": None if security else nres.interaction_id, "fix_report": ""}
            elif is_result and neon["delegation"] == "render_fix" and attempts_left > 0:
                db.update("renders", result.get("render_id"), neon_correction=neon["delegation_brief"])
                delegation = {"mode": "fix", "job_id": job_id, "brief": neon["delegation_brief"],
                              "chain_from": result.get("worker_interaction_id"), "fix_report": critic_report}
            elif is_result:
                # no further attempt: deliver the best iteration and say what is wrong with it
                final_render_id, must_disclose = self._best_render(job_id)
                db.update("render_jobs", job_id, status="gave_up", final_render_id=final_render_id,
                          must_disclose_json=dumps(must_disclose), finished_at=now())

            # 9. speech (silent while a render is in flight)
            if delegation is None:
                # outside a render job, anything he says he did has to carry the episode it comes from
                citable = "\n".join(memory.episode_line(e, 220) for e in memories) + "\n" + memory.persona_detail_lines(db, scope)
                reply, bres, bnotes = await broca.speak(self.llm, persona, winning_thought=thought, must_disclose=must_disclose,
                                                        allowed_ids=allowed, previous_interaction_id=None if security else nres.interaction_id,
                                                        strict_experience=not is_result, evidence=citable, **ctx)
                route.append(_step("Hydrogen", "Broca", bres))
                notes += bnotes
                if session_id:
                    db.update("sessions", session_id, broca_interaction_id=bres.interaction_id, broca_depth=1)

        # 10. episode
        if security:
            importance = max(importance, 8)
        if is_result:
            kind = "render_result"
        elif delegation is not None:
            kind = "render_request"
        else:
            kind = str(gate.get("kind") or "chat")
        eid = memory.add_episode(
            db, scope=scope, day=day, tick=tick_no, kind=kind, source=stim.source, stimulus=clip(stim.text, 4000),
            response=reply or (f"(started render job {job_id}: {clip(delegation['brief'], 300)})" if delegation else ""),
            salience=score, importance=importance, ignited=ignite, gate=gate, neon=neon,
            ws={k: new_ws.get(k, ws.get(k)) for k in gw.SLOT_KEYS}, argon=(result.get("argon") if is_result else argon_up),
            render_job_id=job_id, render_id=result.get("render_id") if is_result else None,
            image_path=result.get("image_path") if is_result else None, injection_flag=security, turn_id=turn_id,
            run_id=run_id, topic=stim.topic)
        if delegation and delegation["mode"] == "new":
            db.update("render_jobs", job_id, origin_episode_id=eid)
        memory.save_workspace(db, scope, **new_ws)
        self.emit(scope, "episode", {"episode_id": eid, "tick": tick_no, "day": day, "kind": kind, "importance": importance,
                                     "ignited": ignite, "reply": reply, "route": route, "notes": notes, "job_id": job_id,
                                     "final_render_id": final_render_id,
                                     "workspace": {k: new_ws.get(k, ws.get(k)) for k in gw.SLOT_KEYS}}, **ev)
        return TickOutcome(episode_id=eid, tick=tick_no, reply=reply, delegation=delegation, ignited=ignite, score=score,
                           gate=gate, neon=neon, retrieved_ids=retrieved_ids, job_id=job_id, final_render_id=final_render_id,
                           route=route, notes=notes)

    # ------------------------------------------------------------ renders

    def _best_render(self, job_id: int) -> tuple[int | None, list[str]]:
        """The iteration to deliver when no clean one exists, and what must be said about it."""
        rows = [rowdict(r) for r in self.db.q("SELECT * FROM renders WHERE job_id=? ORDER BY iteration", (job_id,))]
        with_image = [r for r in rows if r.get("image_path")]
        if not with_image:
            return None, ["no_image"]

        def badness(r: dict) -> tuple:
            argon = loads(r.get("argon_json"), {}) or {}
            sev = max([int(d.get("severity") or 1) for d in argon.get("defects") or []] or [0])
            return (1 if r.get("hard_fail") else 0, sev, -int(r.get("iteration") or 0))

        best = min(with_image, key=badness)
        argon = loads(best.get("argon_json"), {}) or {}
        classes = [d.get("type") for d in argon.get("defects") or [] if d.get("type")]
        if best.get("hard_fail") and "blank_or_uniform" not in classes:
            classes.append("blank_or_uniform")
        if argon.get("matches_request") is False and "wrong_subject" not in classes:
            classes.append("wrong_subject")
        return best["id"], list(dict.fromkeys(classes)) or ["other"]

    def _process(self, files: list[tuple[str, bytes]], rel_dir: str, iteration: int) -> dict:
        """Decode, measure and store what the sandbox returned. Runs in a worker thread."""
        out: dict = {"image_path": None, "sheet_path": None, "gif_path": None, "n_frames": 0, "stats": {}, "hard_fail": None,
                     "reject": None, "model_png": None, "measured": []}
        frames = []
        for name, data in files[: config.SANDBOX_MAX_FRAMES]:
            try:
                frames.append(imaging.decode_png(data))
            except imaging.ImageRejected as exc:
                out["reject"] = f"{name}: {exc}"
        if not frames:
            out["reject"] = out["reject"] or "no decodable PNG was produced"
            return out
        base = self.renders_dir / rel_dir
        base.mkdir(parents=True, exist_ok=True)
        out["n_frames"] = len(frames)
        if len(frames) == 1:
            st = imaging.stats(frames[0])
            imaging.save_png(frames[0], base / f"it{iteration}.png")
            out.update({"image_path": f"{rel_dir}/it{iteration}.png", "stats": st, "measured": imaging.detect(st),
                        "hard_fail": imaging.hard_fail(st), "model_png": imaging.for_model(frames[0])})
            return out
        idx = imaging.key_frames(len(frames))
        per = {i: imaging.stats(frames[i]) for i in idx}
        sheet = imaging.contact_sheet(frames, idx)
        imaging.save_png(sheet, base / f"it{iteration}_sheet.png")
        imaging.save_png(frames[len(frames) // 2], base / f"it{iteration}.png")
        imaging.save_gif(frames, base / f"it{iteration}.gif")
        measured = {i: imaging.detect(per[i]) for i in idx}
        blank = [i for i in idx if imaging.hard_fail(per[i])]
        out.update({"image_path": f"{rel_dir}/it{iteration}.png", "sheet_path": f"{rel_dir}/it{iteration}_sheet.png",
                    "gif_path": f"{rel_dir}/it{iteration}.gif", "stats": {"frames": {str(i): per[i] for i in idx}},
                    "measured": sorted({c for v in measured.values() for c in v}),
                    "measured_frames": {str(i): v for i, v in measured.items() if v},
                    "hard_fail": ("every key frame is blank or one flat colour" if len(blank) == len(idx) else None),
                    "model_png": imaging.for_model(sheet, 1280)})
        return out

    async def render_iteration(self, d: dict, *, scope: str, context: str, session_id: str | None = None,
                               turn_id: int | None = None, run_id: int | None = None) -> dict:
        """Worker writes (or fixes) the script, the sandbox runs it, the app measures the image, Argon inspects it."""
        db = self.db
        ctx = {"context": context, "scope": scope, "turn_id": turn_id, "run_id": run_id}
        ev = {"session_id": session_id, "turn_id": turn_id}
        job = rowdict(db.one("SELECT * FROM render_jobs WHERE id=?", (d["job_id"],)))
        iteration = int(job.get("iterations") or 0) + 1
        rel_dir = job.get("dir") or f"job{job['id']}"
        persona = memory.persona_block(db)
        self.emit(scope, "render_start", {"job_id": job["id"], "iteration": iteration, "mode": d["mode"], "brief": clip(d["brief"], 400)}, **ev)
        wdata, wres = await worker.write_code(self.llm, persona, brief=d["brief"], fix_report=d.get("fix_report", ""),
                                              previous_interaction_id=d.get("chain_from"), **ctx)
        code = wdata["code"]
        prev = [rowdict(r) for r in db.q("SELECT code, sandbox_status, stderr_tail, argon_json FROM renders WHERE job_id=? ORDER BY iteration", (job["id"],))]
        code_hash = hashlib.sha1(code.encode()).hexdigest()
        self.emit(scope, "render_code", {"job_id": job["id"], "iteration": iteration, "approach": wdata["approach"], "view": wdata["view"],
                                         "fractal_type": wdata["fractal_type"], "output_kind": wdata["output_kind"], "code": clip(code, 6000)}, **ev)
        async with self.render_sem:
            sres = await self.sandbox.run(code, frames=wdata["output_kind"] == "gif")
        proc = await asyncio.to_thread(self._process, sres.files, rel_dir, iteration) if sres.files else {
            "image_path": None, "sheet_path": None, "gif_path": None, "n_frames": 0, "stats": {}, "hard_fail": None,
            "reject": None, "model_png": None, "measured": []}
        argon: dict | None = None
        sandbox_message = ""
        if sres.status != "ok" or proc.get("reject"):
            sandbox_message = clip(proc.get("reject") or sres.message, 1100)
        if proc.get("model_png"):
            hints = ""
            if self.argon_hints:
                hints = dumps({"measured_defect_classes": proc.get("measured"), "per_frame": proc.get("measured_frames", {}),
                               "statistics": proc.get("stats") if proc.get("n_frames") == 1 else {}})
            argon, ares = await vision.critique(self.llm, persona, proc["model_png"], request=job.get("request_text") or "",
                                                iteration=iteration, n_frames=proc["n_frames"], view=wdata["view"], hints=hints,
                                                second_look=iteration >= 2, **ctx)
        verdict = (argon or {}).get("verdict", "fail")
        if (proc.get("hard_fail") or sres.status != "ok") and verdict == "pass":
            verdict = "fix"                     # a script that was stopped or crashed has not passed, whatever its partial output looks like
        # The critic sometimes passes an image because it matches the literal request while listing a serious
        # defect in the same breath. A listed defect of severity 3 or more always goes back to the workspace.
        if verdict == "pass" and any(int(d.get("severity") or 0) >= 3 for d in (argon or {}).get("defects") or []):
            verdict = "fix"
            argon["verdict_overridden"] = "the critic said pass while listing a defect of severity 3 or more"
        repeat = any(hashlib.sha1((p.get("code") or "").encode()).hexdigest() == code_hash for p in prev) or (
            sres.status != "ok" and any(p.get("sandbox_status") == sres.status and (p.get("stderr_tail") or "")[-200:] == sres.stderr_tail[-200:] for p in prev))
        previous_defects = "; ".join(sorted({d0.get("type", "") for p in prev for d0 in (loads(p.get("argon_json"), {}) or {}).get("defects", [])}))
        render_id = db.insert("renders", job_id=job["id"], iteration=iteration, approach=wdata["approach"], view=wdata["view"],
                              code=code, sandbox_status=sres.status, stderr_tail=clip(sres.stderr_tail, 1500), wall_ms=sres.wall_ms,
                              image_path=proc.get("image_path"), sheet_path=proc.get("sheet_path"), gif_path=proc.get("gif_path"),
                              n_frames=proc.get("n_frames") or 0, stats_json=dumps({"stats": proc.get("stats"), "measured": proc.get("measured")}),
                              hard_fail=proc.get("hard_fail"), argon_json=dumps(argon) if argon else None, argon_verdict=verdict,
                              worker_interaction_id=wres.interaction_id, created_at=now())
        db.update("render_jobs", job["id"], iterations=iteration, fractal_type=wdata["fractal_type"])
        self.emit(scope, "render_image", {"job_id": job["id"], "render_id": render_id, "iteration": iteration,
                                          "image_path": proc.get("image_path"), "gif_path": proc.get("gif_path"),
                                          "sandbox_status": sres.status, "sandbox_message": clip(sandbox_message, 300),
                                          "wall_ms": sres.wall_ms, "hard_fail": proc.get("hard_fail")}, **ev)
        if argon:
            self.emit(scope, "argon", {"job_id": job["id"], "render_id": render_id, "iteration": iteration, **argon}, **ev)
        return {"job_id": job["id"], "render_id": render_id, "iteration": iteration, "sandbox_status": sres.status,
                "sandbox_message": sandbox_message, "hard_fail": proc.get("hard_fail"), "verdict": verdict,
                "matches_request": (argon or {}).get("matches_request", False), "argon": argon, "view": wdata["view"],
                "worker_interaction_id": wres.interaction_id, "image_path": proc.get("image_path"), "repeat": repeat,
                "previous_defects": previous_defects, "worker_step": _step("Hydrogen", "worker (code)", wres)}

    @staticmethod
    def result_stimulus(res: dict) -> Stimulus:
        argon = res.get("argon") or {}
        if res.get("sandbox_status") != "ok" or not res.get("image_path"):
            text = f"Render attempt {res['iteration']} of job {res['job_id']} produced no usable image: {res.get('sandbox_message')}"
        else:
            defects = ", ".join(f"{d['type']} (severity {d['severity']})" for d in argon.get("defects") or []) or "none"
            text = (f"Render attempt {res['iteration']} of job {res['job_id']} finished. Critic's verdict: {res['verdict']}; "
                    f"defects: {defects}. Seen: {argon.get('observations', '')}")
            if res.get("hard_fail"):
                text += f" Measured: {res['hard_fail']}."
        return Stimulus(text=text, source="render_result", result=res, external=False)

    # ------------------------------------------------------------ a whole conversational turn

    async def run_turn(self, *, scope: str, text: str, context: str, source: str = "visitor", session_id: str | None = None,
                       turn_id: int | None = None, image_png: bytes | None = None, upload_id: int | None = None,
                       max_renders: int = config.MAX_RENDERS_VISITOR, deadline_s: float = config.TURN_DEADLINE_S,
                       allow_render: bool = True, run_id: int | None = None, topic: str = "") -> dict:
        """One message in, one reply out. Renders started by the turn are awaited inside it."""
        t0 = time.monotonic()
        async with self.lock(scope):
            pending = [Stimulus(text=text, source=source, image_png=image_png, upload_id=upload_id, topic=topic)]
            outcome: TickOutcome | None = None
            episodes: list[int] = []
            job_id = None
            renders = 0
            first_score: int | None = None
            ignited_any = False
            while pending:
                stim = pending.pop(0)
                left = deadline_s - (time.monotonic() - t0)
                outcome = await self.tick(scope, stim, context=context, session_id=session_id, turn_id=turn_id, run_id=run_id,
                                          max_renders=max_renders, time_left=left, allow_render=allow_render)
                episodes.append(outcome.episode_id)
                job_id = outcome.job_id or job_id
                first_score = outcome.score if first_score is None else first_score
                ignited_any = ignited_any or outcome.ignited
                if outcome.delegation:
                    res = await self.render_iteration(outcome.delegation, scope=scope, context=context, session_id=session_id,
                                                      turn_id=turn_id, run_id=run_id)
                    renders += 1
                    pending.append(self.result_stimulus(res))
            assert outcome is not None
            return {"reply": outcome.reply or "", "episode_ids": episodes, "job_id": job_id, "renders": renders,
                    "final_render_id": outcome.final_render_id, "score": first_score, "ignited": ignited_any,
                    "latency_ms": int((time.monotonic() - t0) * 1000)}

    # ------------------------------------------------------------ sleep

    async def sleep(self, scope: str, day: int, *, context: str, trigger: str = "schedule", run_id: int | None = None,
                    session_id: str | None = None) -> dict:
        """Carbon's offline pass: replay the day's important episodes and distil what they support."""
        db = self.db
        ctx = {"context": context, "scope": scope, "run_id": run_id, "turn_id": None}
        last = int(db.kv_get(f"sleep_cursor:{scope}", "0") or 0)
        upto = int(db.val("SELECT COALESCE(MAX(id),0) FROM episodes WHERE scope=?", (scope,), 0))
        rows = [rowdict(r) for r in db.q(
            "SELECT * FROM episodes WHERE scope=? AND id>? AND (spwr=1 OR kind IN ('render_result','fact_update','pushback','injection_attempt') OR injection_flag=1) ORDER BY id",
            (scope, last))]
        if len(rows) < 4:
            rows = [rowdict(r) for r in db.q("SELECT * FROM episodes WHERE scope=? AND id>? ORDER BY id", (scope, last))]
        if len(rows) > 28:
            keep = sorted(sorted(rows, key=lambda e: (e["importance"], e["id"]), reverse=True)[:28], key=lambda e: e["id"])
            rows = keep
        sleep_id = db.insert("sleeps", scope=scope, day=day, replayed_ids_json=dumps([e["id"] for e in rows]), trigger=trigger,
                             status="running", started_at=now())
        self.emit(scope, "sleep_start", {"day": day, "replayed": [e["id"] for e in rows]}, session_id=session_id)
        if not rows:
            db.update("sleeps", sleep_id, status="done", finished_at=now(), accepted_json="{}", dropped_json="[]")
            return {"sleep_id": sleep_id, "accepted": {}, "dropped": 0}

        persona = memory.persona_block(db)
        data, res = await dmn.consolidate(self.llm, persona, day=day, replayed=rows, prefs=memory.active_preferences(db, scope, 30),
                                          facts=memory.live_facts(db, scope, 30), **ctx)
        accepted, dropped = memory.apply_sleep(db, scope, day, data, rows, sleep_id)
        db.kv_set(f"sleep_cursor:{scope}", str(upto))        # only now: an interrupted sleep is simply run again
        db.update("sleeps", sleep_id, raw_json=dumps(data), accepted_json=dumps(accepted), dropped_json=dumps(dropped),
                  status="done", finished_at=now())
        if scope == CANON:
            memory.snapshot(db, day, sleep_id)
        self.emit(scope, "sleep_done", {"day": day, "sleep_id": sleep_id, "accepted": accepted, "dropped": len(dropped),
                                        "day_summary": clip(data.get("day_summary"), 400), "ms": res.latency_ms,
                                        "route": [_step("Carbon", "sleep consolidation", res)]}, session_id=session_id)
        return {"sleep_id": sleep_id, "accepted": accepted, "dropped": len(dropped), "summary": data.get("day_summary", "")}


def _who(source: str) -> str:
    return {"tutor": "tutor", "owner": "person", "eval": "person"}.get(source, "person")
