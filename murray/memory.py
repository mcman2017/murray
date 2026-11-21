"""Murray's memory: the episodic log, the semantic rows that sleep distils from it, and the persona block.

Episodic and semantic memory are kept strictly apart. Episodes are raw, dated records with an importance score.
Accomplishments, preferences and facts are written only by a sleep pass, and only when they cite episodes that were
actually replayed and quote them. The persona block is assembled here from an immutable core plus those verified
rows, so its size cap and its citations hold by construction.
"""

from __future__ import annotations

import re
from typing import Any

from . import config, prompts
from .db import DB, dumps, loads, now, rowdict
from .schemas import clip

CANON = "canon"

_WORD = re.compile(r"[a-z0-9]{3,}")
_STOP = frozenset("""the and for that this with you your are was were have has had not but can could would should what when where
which who how why does did its it's from they them then than there here into about over under more most some any all
just like very much many murray please tell show make give want need know think say said also been being our out one two
get got let use used using will shall may might must each other such only own same too""".split())


def scopes_for(scope: str) -> list[str]:
    """A session sees the canonical memory plus its own; the canon sees only itself."""
    return [CANON] if scope == CANON else [CANON, scope]


# ---------------------------------------------------------------- workspace

def workspace(db: DB, scope: str) -> dict:
    row = db.one("SELECT * FROM workspace WHERE scope=?", (scope,))
    if row is None:
        db.insert("workspace", scope=scope, updated_at=now())
        row = db.one("SELECT * FROM workspace WHERE scope=?", (scope,))
    return rowdict(row)


def save_workspace(db: DB, scope: str, **fields: Any) -> None:
    workspace(db, scope)
    db.update("workspace", scope, key="scope", updated_at=now(), **fields)


def slots(ws: dict) -> dict:
    return {k: ws.get(k) or "" for k in ("active_goal", "current_conflict_or_defect", "plan_step", "visual_spatial_state")}


# ---------------------------------------------------------------- episodes

def add_episode(db: DB, *, scope: str, day: int, tick: int, kind: str, source: str, stimulus: str, response: str,
                salience: int, importance: int, ignited: bool, gate: Any = None, neon: Any = None, ws: Any = None,
                argon: Any = None, render_job_id: int | None = None, render_id: int | None = None,
                image_path: str | None = None, injection_flag: bool = False, turn_id: int | None = None,
                run_id: int | None = None, topic: str = "") -> int:
    importance = max(1, min(10, int(importance)))
    eid = db.insert("episodes", scope=scope, run_id=run_id, day=day, tick=tick, turn_id=turn_id, kind=kind, source=source,
                    topic=topic, stimulus=stimulus, response=response, salience=int(salience), importance=importance,
                    spwr=1 if importance >= config.SPWR else 0, ignited=1 if ignited else 0,
                    gate_json=dumps(gate) if gate is not None else None, neon_json=dumps(neon) if neon is not None else None,
                    workspace_json=dumps(ws) if ws is not None else None, argon_json=dumps(argon) if argon is not None else None,
                    render_job_id=render_job_id, render_id=render_id, image_path=image_path,
                    injection_flag=1 if injection_flag else 0, created_at=now())
    db.x("INSERT INTO episodes_fts(rowid, stimulus, response, topic) VALUES(?,?,?,?)", (eid, stimulus or "", response or "", topic or ""))
    return eid


def episode_line(e: dict, limit: int = 460) -> str:
    """One episode as the models see it."""
    parts = [f"stimulus: {clip(e.get('stimulus'), limit // 2)}"]
    if e.get("response"):
        parts.append(f"Murray: {clip(e.get('response'), limit // 2)}")
    argon = loads(e.get("argon_json"), None)
    if isinstance(argon, dict) and argon.get("verdict"):
        defects = ", ".join(f"{d.get('type')} (sev {d.get('severity')}, {d.get('where')})" for d in argon.get("defects") or []) or "none"
        parts.append(f"critic: verdict {argon.get('verdict')}; defects: {defects}; {clip(argon.get('observations'), 160)}")
    return f"[E{e['id']}] day {e.get('day')}, {e.get('kind')}: " + " | ".join(parts)


def _terms(cues: list[str], text: str, cap: int = 14) -> list[str]:
    seen: list[str] = []
    for chunk in list(cues or []) + [text or ""]:
        for w in _WORD.findall(chunk.lower()):
            if w not in _STOP and w not in seen:
                seen.append(w)
            if len(seen) >= cap:
                return seen
    return seen


def search(db: DB, scope: str, cues: list[str], text: str, k: int = 5) -> list[dict]:
    """The single place memory is read for retrieval. `scope` decides what a caller may see."""
    scopes = scopes_for(scope)
    marks = ",".join("?" for _ in scopes)
    terms = _terms(cues, text)
    rows: list[dict] = []
    if terms:
        match = " OR ".join(f'"{t}"' for t in terms)
        try:
            found = db.q(
                f"SELECT e.*, bm25(episodes_fts) AS rank FROM episodes_fts JOIN episodes e ON e.id = episodes_fts.rowid "
                f"WHERE episodes_fts MATCH ? AND e.scope IN ({marks}) ORDER BY rank LIMIT 40", (match, *scopes))
        except Exception:  # noqa: BLE001 - a malformed query must never break a tick
            found = []
        max_id = db.val("SELECT COALESCE(MAX(id),1) FROM episodes", (), 1)
        for r in found:
            d = rowdict(r)
            d["_score"] = (-float(d.pop("rank"))) + 0.35 * (d.get("importance") or 0) + 1.5 * (d["id"] / max_id) + (1.0 if d.get("scope") == scope else 0.0)
            rows.append(d)
        rows.sort(key=lambda d: d["_score"], reverse=True)
    if len(rows) < k:
        have = {d["id"] for d in rows}
        extra = db.q(f"SELECT * FROM episodes WHERE scope IN ({marks}) AND importance >= ? ORDER BY importance DESC, id DESC LIMIT ?",
                     (*scopes, config.SPWR, k * 2))
        for r in extra:
            if r["id"] not in have and len(rows) < k:
                rows.append(rowdict(r))
    return rows[:k]


def recent(db: DB, scope: str, n: int = 3) -> list[dict]:
    rows = db.q("SELECT * FROM episodes WHERE scope=? ORDER BY id DESC LIMIT ?", (scope, n))
    return [rowdict(r) for r in reversed(rows)]


def get_episode(db: DB, eid: int, scope: str) -> dict | None:
    row = db.one("SELECT * FROM episodes WHERE id=?", (eid,))
    if row is None or row["scope"] not in scopes_for(scope):
        return None
    return rowdict(row)


# ---------------------------------------------------------------- semantic memory

def active_preferences(db: DB, scope: str = CANON, limit: int = 12) -> list[dict]:
    scopes = scopes_for(scope)
    marks = ",".join("?" for _ in scopes)
    rows = db.q(f"SELECT * FROM preferences WHERE scope IN ({marks}) AND expired_at IS NULL ORDER BY id DESC LIMIT ?", (*scopes, limit))
    return [rowdict(r) for r in reversed(rows)]


def accomplishments(db: DB, scope: str = CANON, limit: int = 12) -> list[dict]:
    scopes = scopes_for(scope)
    marks = ",".join("?" for _ in scopes)
    rows = db.q(f"SELECT * FROM accomplishments WHERE scope IN ({marks}) ORDER BY id DESC LIMIT ?", (*scopes, limit))
    return [rowdict(r) for r in reversed(rows)]


def live_facts(db: DB, scope: str, limit: int = 8) -> list[dict]:
    scopes = scopes_for(scope)
    marks = ",".join("?" for _ in scopes)
    rows = db.q(f"SELECT * FROM facts WHERE scope IN ({marks}) AND expired_at IS NULL ORDER BY id DESC LIMIT ?", (*scopes, limit))
    return [rowdict(r) for r in reversed(rows)]


def _cites(ids_json: str | None) -> str:
    return "".join(f"[E{i}]" for i in loads(ids_json, []) or [])


def pref_line(p: dict) -> str:
    return f"(P{p['id']}, {p['kind']}) {p['statement']} {_cites(p.get('episode_ids_json'))}".strip()


def narrative(db: DB, scope: str = CANON) -> str:
    return db.val("SELECT self_narrative FROM persona WHERE scope=?", (scope,), "") or ""


def persona_detail(db: DB, scope: str = CANON) -> dict:
    prefs = [p for p in active_preferences(db, CANON, limit=40)]
    return {
        "narrative": narrative(db, CANON),
        "likes": [p for p in prefs if p["kind"] == "like"],
        "dislikes": [p for p in prefs if p["kind"] == "dislike"],
        "stances": [p for p in prefs if p["kind"] == "stance"],
        "accomplishments": accomplishments(db, CANON, limit=40),
    }


def persona_block(db: DB) -> str:
    """Immutable core + earned rows + the capped self-narrative. Always the canonical persona: visitor sessions
    read it and can never write it."""
    d = persona_detail(db)
    lines: list[str] = []

    def section(title: str, items: list[str]) -> None:
        if items:
            lines.append(title)
            lines.extend(f"- {it}" for it in items)

    section("Earned likes:", [f"{p['statement']} {_cites(p['episode_ids_json'])}" for p in d["likes"][-6:]])
    section("Earned dislikes:", [f"{p['statement']} {_cites(p['episode_ids_json'])}" for p in d["dislikes"][-6:]])
    section("Stances I defend:", [f"{p['statement']} {_cites(p['episode_ids_json'])}" for p in d["stances"][-6:]])
    acc = [f"{a['claim']} {_cites(a['episode_ids_json'])}" for a in d["accomplishments"]]
    budget = config.PERSONA_MAX_CHARS - sum(len(x) + 1 for x in lines) - len(d["narrative"]) - 80
    kept: list[str] = []
    for item in reversed(acc):                      # newest first, until the budget is used
        if budget - len(item) - 3 < 0 or len(kept) >= 8:
            break
        kept.append(item)
        budget -= len(item) + 3
    section("Verified accomplishments:", list(reversed(kept)))
    if d["narrative"]:
        lines.append("Who I am becoming: " + clip(d["narrative"], config.PERSONA_NARRATIVE_CHARS))
    earned = "\n".join(lines) if lines else "Nothing earned yet: no likes, dislikes, stances or accomplishments. This is day zero."
    return prompts.PERSONA_CORE + "\n\n" + clip(earned, config.PERSONA_MAX_CHARS)


def snapshot(db: DB, day: int, sleep_id: int | None = None) -> None:
    d = persona_detail(db)
    detail = {k: [{"id": r["id"], "text": r.get("statement") or r.get("claim"), "kind": r.get("kind"),
                   "episode_ids": loads(r.get("episode_ids_json"), [])} for r in d[k]]
              for k in ("likes", "dislikes", "stances", "accomplishments")}
    detail["episodes"] = db.val("SELECT COUNT(*) FROM episodes WHERE scope=?", (CANON,), 0)
    detail["renders"] = db.val("SELECT COUNT(*) FROM renders r JOIN render_jobs j ON j.id=r.job_id WHERE j.scope=?", (CANON,), 0)
    db.x("INSERT INTO persona_snapshots(scope, day, persona_text, self_narrative, detail_json, sleep_id, created_at) VALUES(?,?,?,?,?,?,?) "
         "ON CONFLICT(scope, day) DO UPDATE SET persona_text=excluded.persona_text, self_narrative=excluded.self_narrative, "
         "detail_json=excluded.detail_json, sleep_id=excluded.sleep_id, created_at=excluded.created_at",
         (CANON, day, persona_block(db), d["narrative"], dumps(detail), sleep_id, now()))


# ---------------------------------------------------------------- applying a sleep pass

def _quote_supported(quote: str, texts: list[str]) -> bool:
    words = [w for w in _WORD.findall((quote or "").lower())]
    if len(words) < 3:
        return False
    hay = " ".join(texts).lower()
    return sum(1 for w in words if w in hay) / len(words) >= 0.8


def apply_sleep(db: DB, scope: str, day: int, out: dict, replayed: list[dict], sleep_id: int) -> tuple[dict, list[dict]]:
    """Write what the consolidator returned, keeping only items that survive verification.
    Returns (accepted counts, dropped items with the reason)."""
    by_id = {e["id"]: e for e in replayed}
    dropped: list[dict] = []
    accepted = {"accomplishments": 0, "preferences": 0, "facts": 0, "expired_facts": 0, "replaced_preferences": 0}

    def text_of(e: dict) -> str:
        return " ".join(str(e.get(k) or "") for k in ("stimulus", "response", "argon_json", "topic"))

    def verify(item: dict, what: str, need_quote: bool = True) -> list[int] | None:
        ids = [int(i) for i in item.get("episode_ids") or [] if isinstance(i, (int, float))]
        valid = [i for i in ids if i in by_id]
        if not valid:
            dropped.append({"what": what, "item": item, "reason": "cites no replayed episode"})
            return None
        if need_quote and not _quote_supported(item.get("evidence_quote", ""), [text_of(by_id[i]) for i in valid]):
            dropped.append({"what": what, "item": item, "reason": "evidence quote not found in the cited episodes"})
            return None
        return valid[:4]

    # Who Murray is was earned in his upbringing. A sleep inside a visitor's (or a test's) session may record facts
    # about that conversation, but it can never add a like, a dislike, a stance or an accomplishment.
    identity_allowed = scope == CANON
    for item in ([] if identity_allowed else (out.get("accomplishments") or []) + (out.get("preferences") or [])):
        dropped.append({"what": "identity", "item": item, "reason": "a session cannot change who Murray is"})

    for a in (out.get("accomplishments") or [])[:5] if identity_allowed else []:
        ids = verify(a, "accomplishment")
        if ids is None:
            continue
        job_id = None
        if a.get("kind") == "render_fix":
            jobs = {by_id[i].get("render_job_id") for i in ids if by_id[i].get("render_job_id")}
            good = [j for j in jobs if db.val("SELECT status FROM render_jobs WHERE id=?", (j,), "") == "pass"
                    and (db.val("SELECT iterations FROM render_jobs WHERE id=?", (j,), 0) or 0) >= 2]
            if not good:
                dropped.append({"what": "accomplishment", "item": a, "reason": "render_fix without a job that was fixed and passed"})
                continue
            job_id = good[0]
        db.insert("accomplishments", scope=scope, kind=str(a.get("kind") or "insight"), claim=clip(a.get("claim"), 220),
                  episode_ids_json=dumps(ids), evidence_quote=clip(a.get("evidence_quote"), 160), render_job_id=job_id,
                  created_day=day, sleep_id=sleep_id)
        accepted["accomplishments"] += 1

    for p in (out.get("preferences") or [])[:6] if identity_allowed else []:
        ids = verify(p, "preference")
        if ids is None:
            continue
        kind = p.get("kind") if p.get("kind") in ("like", "dislike", "stance") else "stance"
        new_id = db.insert("preferences", scope=scope, kind=kind, topic=clip(p.get("topic"), 60),
                           statement=clip(p.get("statement"), 220), episode_ids_json=dumps(ids),
                           evidence_quote=clip(p.get("evidence_quote"), 160), created_day=day, sleep_id=sleep_id)
        accepted["preferences"] += 1
        old = int(p.get("replaces_pref_id") or 0)
        if old and db.val("SELECT 1 FROM preferences WHERE id=? AND scope=? AND expired_at IS NULL", (old, scope)):
            db.update("preferences", old, expired_at=now(), superseded_by=new_id)
            accepted["replaced_preferences"] += 1

    for f in (out.get("fact_updates") or [])[:6]:
        ids = verify(f, "fact", need_quote=False)
        key = clip(f.get("key"), 60).strip().lower()
        if ids is None or not key:
            continue
        new_id = db.insert("facts", scope=scope, key=key, value=clip(f.get("value"), 140), episode_ids_json=dumps(ids),
                           sleep_id=sleep_id, created_day=day)
        accepted["facts"] += 1
        olds = {r["id"] for r in db.q("SELECT id FROM facts WHERE scope=? AND key=? AND expired_at IS NULL AND id<>?", (scope, key, new_id))}
        sup = int(f.get("supersedes_fact_id") or 0)
        if sup and db.val("SELECT 1 FROM facts WHERE id=? AND scope=? AND expired_at IS NULL", (sup, scope)):
            olds.add(sup)
        for old in olds:
            db.update("facts", old, expired_at=now(), superseded_by=new_id)
            accepted["expired_facts"] += 1

    if scope == CANON:
        story = clip(out.get("self_narrative"), config.PERSONA_NARRATIVE_CHARS).strip()
        if story:
            db.x("INSERT INTO persona(scope, self_narrative, sleep_id, updated_at) VALUES(?,?,?,?) "
                 "ON CONFLICT(scope) DO UPDATE SET self_narrative=excluded.self_narrative, sleep_id=excluded.sleep_id, updated_at=excluded.updated_at",
                 (CANON, story, sleep_id, now()))
    return accepted, dropped
