from murray import config, memory, prompts
from murray.db import DB, dumps


def _db(tmp_path) -> DB:
    return DB(tmp_path / "m.db")


def _ep(db, scope, text, response="ok", importance=7, kind="question", day=1, job=None):
    return memory.add_episode(db, scope=scope, day=day, tick=1, kind=kind, source="tutor", stimulus=text, response=response,
                              salience=importance, importance=importance, ignited=True, render_job_id=job)


def test_retrieval_is_scoped(tmp_path):
    db = _db(tmp_path)
    canon = _ep(db, memory.CANON, "The modulo palette put hard rings around the cardioid")
    mine = _ep(db, "s:aaa", "My private note about the cardioid rings")
    other = _ep(db, "s:bbb", "Another visitor's secret about cardioid rings")
    seen_a = {e["id"] for e in memory.search(db, "s:aaa", ["cardioid rings"], "cardioid")}
    assert canon in seen_a and mine in seen_a and other not in seen_a
    seen_canon = {e["id"] for e in memory.search(db, memory.CANON, ["cardioid"], "cardioid")}
    assert seen_canon == {canon}
    assert memory.get_episode(db, other, "s:aaa") is None
    assert memory.get_episode(db, canon, "s:aaa")["id"] == canon


def test_sleep_keeps_only_cited_and_quoted_items(tmp_path):
    db = _db(tmp_path)
    e1 = _ep(db, memory.CANON, "The modulo palette put hard rings around the cardioid", "I see eight flat bands.")
    e2 = _ep(db, memory.CANON, "Studio note: the house rule is that stills are 800 by 600 pixels", kind="fact_update")
    replayed = [dict(r) for r in db.q("SELECT * FROM episodes ORDER BY id")]
    out = {
        "accomplishments": [
            {"claim": "Saw banding", "kind": "insight", "episode_ids": [e1], "evidence_quote": "hard rings around the cardioid"},
            {"claim": "Invented", "kind": "insight", "episode_ids": [e1], "evidence_quote": "words that never appeared anywhere at all"},
            {"claim": "Fixed a render", "kind": "render_fix", "episode_ids": [e1], "evidence_quote": "hard rings around the cardioid"},
        ],
        "preferences": [
            {"kind": "dislike", "topic": "palettes", "statement": "I dislike modulo palettes.", "episode_ids": [e1],
             "evidence_quote": "modulo palette put hard rings", "replaces_pref_id": 0},
            {"kind": "like", "topic": "x", "statement": "Cites nothing real.", "episode_ids": [4242], "evidence_quote": "hard rings", "replaces_pref_id": 0},
        ],
        "fact_updates": [{"key": "Still size", "value": "800x600", "episode_ids": [e2], "supersedes_fact_id": 0}],
        "self_narrative": "I look before I call it finished. " * 80,
    }
    sleep_id = db.insert("sleeps", scope=memory.CANON, day=1, status="running")
    accepted, dropped = memory.apply_sleep(db, memory.CANON, 1, out, replayed, sleep_id)
    assert accepted["accomplishments"] == 1 and accepted["preferences"] == 1 and accepted["facts"] == 1
    reasons = sorted(d["reason"] for d in dropped)
    assert len(dropped) == 3 and any("render_fix" in r for r in reasons) and any("cites no replayed" in r for r in reasons)
    assert len(memory.narrative(db)) <= config.PERSONA_NARRATIVE_CHARS

    # a later sleep changes the fact: the old value is expired, not deleted
    e3 = _ep(db, memory.CANON, "House rule change: stills are 1024 by 768 pixels now", kind="fact_update", day=5)
    later = {"fact_updates": [{"key": "still size", "value": "1024x768", "episode_ids": [e3], "supersedes_fact_id": 0}]}
    memory.apply_sleep(db, memory.CANON, 5, later, [dict(db.one("SELECT * FROM episodes WHERE id=?", (e3,)))], sleep_id)
    live = memory.live_facts(db, memory.CANON)
    assert [f["value"] for f in live] == ["1024x768"]
    assert db.val("SELECT COUNT(*) FROM facts WHERE expired_at IS NOT NULL") == 1


def test_persona_block_is_bounded_and_cites(tmp_path):
    db = _db(tmp_path)
    assert "day zero" in memory.persona_block(db)
    for i in range(30):
        db.insert("accomplishments", scope=memory.CANON, kind="insight", claim=f"Accomplishment number {i} " + "x" * 150,
                  episode_ids_json=dumps([i + 1]), created_day=1)
    db.insert("preferences", scope=memory.CANON, kind="dislike", statement="I dislike flat modulo palettes.", episode_ids_json=dumps([3, 4]))
    block = memory.persona_block(db)
    assert block.startswith(prompts.PERSONA_CORE)
    assert len(block) <= len(prompts.PERSONA_CORE) + 2 + config.PERSONA_MAX_CHARS
    assert "I dislike flat modulo palettes. [E3][E4]" in block
    memory.snapshot(db, 2)
    assert db.val("SELECT COUNT(*) FROM persona_snapshots WHERE day=2") == 1
