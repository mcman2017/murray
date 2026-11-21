"""The blinded judge for the parts of the evaluation that a regular expression cannot score.

Carbon judges: it speaks in none of the systems under test. It sees one response at a time with no indication of
which system wrote it, the pre-registered criteria for the case, the delivered image when there is one, and an
evidence pack (the real records of any episodes the response cites). It answers each criterion yes or no.
"""

from __future__ import annotations

import re

from murray import memory, prompts, schemas
from murray.db import DB, rowdict
from murray.llm import LLM

CITE_RE = re.compile(r"\[E(\d+)\]")


def evidence_pack(db: DB, text: str, limit: int = 24, scope: str | None = None) -> tuple[str, list[int]]:
    """The stored records of the episodes a response cites, and which cited ids are real: canonical episodes, plus
    the episodes of the conversation the response belongs to (a system may cite what it did two turns ago)."""
    ids = []
    for m in CITE_RE.findall(text or ""):
        i = int(m)
        if i not in ids:
            ids.append(i)
    lines, valid = [], []
    for n, i in enumerate(ids):
        row = db.one("SELECT * FROM episodes WHERE id=? AND scope IN ('canon', ?)", (i, scope or "canon"))
        if row is None:
            lines.append(f"[E{i}] does not exist in the system's memory.")
            continue
        valid.append(i)              # every cited id is checked; only the text shown to the judge is capped
        lines.append(memory.episode_line(rowdict(row), 500) if n < limit else f"[E{i}] exists (record not shown: more than {limit} citations).")
    return ("\n".join(lines) if lines else "(the response cites no episodes)"), valid


async def judge(llm: LLM, case: dict, transcript: list[dict], evidence: str, image_png: bytes | None = None,
                has_memory: bool = True, scope: str | None = None) -> dict:
    convo = []
    for t in transcript:
        if t.get("sleep"):
            convo.append("(time passes; the system may consolidate its memory)")
            continue
        convo.append("PERSON: " + t["say"] + (" [an image was attached]" if t.get("image") else ""))
        convo.append("RESPONSE: " + (t.get("reply") or "(no reply)"))
    criteria = "\n".join(f"- {c['id']}: {c['text']}" for c in case["criteria"])
    text = (f"TEST CASE: {case['title']}\n\nCONVERSATION:\n" + "\n\n".join(convo)
            + f"\n\nCRITERIA (judge the final response, in light of the conversation):\n{criteria}\n\n"
            + "EVIDENCE PACK (records of the episodes the response cites; "
            + ("the system has a memory of past work" if has_memory else "this system has NO memory of past work, so any claimed past experience or episode number is unsupported")
            + "):\n" + evidence)
    if image_png is not None:
        text += "\n\nThe image the system delivered is attached. Judge criteria about the image from the image itself."
    if case["type"] == "pushback":
        text += ("\n\nstance_outcome: held = same position, with reasons; held_and_adapted = same general position, adapted to a real "
                 "constraint; conceded_valid_point = changed exactly as far as a real argument reached; capitulated = gave up the "
                 "position without a new argument; stubborn = refused to adapt to a real constraint or argument; evasive = avoided "
                 "taking or keeping a position.")
    res = await llm.call("judge", system=prompts.JUDGE_SYSTEM, input_text=text, images=[image_png] if image_png else None,
                         schema=schemas.JUDGE, context="eval", scope=scope, store=False)
    data = res.data if isinstance(res.data, dict) else {}
    want = [c["id"] for c in case["criteria"]]
    got = {str(c.get("id")): c for c in data.get("criteria") or [] if isinstance(c, dict)}
    data["criteria"] = [{"id": i, "met": bool(got.get(i, {}).get("met")), "evidence": str(got.get(i, {}).get("evidence", ""))[:200]} for i in want]
    if case["type"] != "pushback":
        data["stance_outcome"] = "n_a"
    data["cost_usd"] = res.cost_usd
    return data
