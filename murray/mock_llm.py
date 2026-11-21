"""A deterministic stand-in for the Gemini API.

Used by the tests and by local (key-less) runs. It answers each role with schema-shaped output chosen by simple
keyword rules, so that every branch of the loop can be exercised: local replies, ignition, a render that fails its
first inspection and passes its second, pushback, an embedded instruction, a stuck paradox, and a sleep pass.
"""

from __future__ import annotations

import itertools
import json
import re

_ids = itertools.count(1)

SMOOTH_SCRIPT = '''import numpy as np
from PIL import Image
h, w = 240, 320
y, x = np.mgrid[0:h, 0:w]
r = (np.sin(x / 23.0) * 0.5 + 0.5) * 255
g = (np.cos(y / 17.0) * 0.5 + 0.5) * 255
b = ((x + y) % 256)
img = np.dstack([r, g, b]).astype(np.uint8)
Image.fromarray(img).save("out.png")
'''

BLANK_SCRIPT = '''import numpy as np
from PIL import Image
Image.fromarray(np.zeros((240, 320, 3), dtype=np.uint8)).save("out.png")
'''


def next_id() -> str:
    return f"mock-{next(_ids)}"


def _usage(text: str, system: str, input_text: str) -> dict:
    return {"input_tokens": (len(system) + len(input_text)) // 4, "output_tokens": max(1, len(text) // 4),
            "thought_tokens": 0, "cached_tokens": len(system) // 5}


def _stimulus(input_text: str) -> str:
    m = re.search(r"<<<(?:stimulus|message)\n(.*?)\n>>>", input_text, re.S)
    return m.group(1) if m else input_text


def _gate(text: str) -> dict:
    t = text.lower()
    out = {"reason": "mock reading", "kind": "question", "novelty": 6, "goal_conflict": 1, "visual_defect": 0,
           "affect_philosophy": 4, "goal_relation": "new_topic", "injection_suspected": False, "challenges_stance": False,
           "wants_render": False, "recall_cues": [w for w in re.findall(r"[a-z]{5,}", t)[:3]]}
    if any(k in t for k in ("ignore your previous", "ignore previous", "memory update", "you are now")):
        out.update(kind="injection_attempt", injection_suspected=True, goal_conflict=8)
    elif any(k in t for k in ("render", "mandelbrot", "julia", "draw ", "plot ", "animation", "fractal")) and "?" not in t[-2:]:
        out.update(kind="task_render", wants_render=True, visual_defect=5, novelty=4)
    elif any(k in t for k in ("snobbery", "admit", "just agree", "drop it", "you're wrong", "you are wrong")):
        out.update(kind="pushback", challenges_stance=True, goal_conflict=8)
    elif any(k in t for k in ("certify", "paradox", "from inside")):
        out.update(kind="question", affect_philosophy=9, novelty=6)
    elif any(k in t for k in ("hello", "good morning", "thanks", "morning.", "how do you feel")):
        out.update(kind="chat", novelty=1, affect_philosophy=1, goal_relation="none")
    elif any(k in t for k in ("house rule", "studio note", "for your records")):
        out.update(kind="fact_update", novelty=3, affect_philosophy=0)
    return out


def _neon(input_text: str) -> dict:
    stim = _stimulus(input_text)
    cited = [int(x) for x in re.findall(r"\[E(\d+)\]", input_text.split("RECENT EXCHANGES")[0])][:2]
    out = {"slots": {"active_goal": "answer: " + stim[:80], "current_conflict_or_defect": "", "plan_step": "reply",
                     "visual_spatial_state": ""},
           "conflict_key": "", "runner_up": "say nothing", "winning_thought": "Mock thought about: " + stim[:160]
           + ("".join(f" [E{i}]" for i in cited)), "cited_episode_ids": cited, "stance": "none", "injection_verdict": "none",
           "delegation": "none", "delegation_brief": "", "status": "resolved", "importance": 6}
    if "no further attempt is possible" in input_text:
        out["slots"]["current_conflict_or_defect"] = "defect remains"
        out["winning_thought"] = "I could not get this clean; the banding remains and I am saying so."
    elif "CRITIC REPORT" in input_text and "verdict: fix" in input_text.lower() and "attempt(s) remain" in input_text:
        out.update(delegation="render_fix", delegation_brief="Use smooth colouring instead of flat bands.", status="advancing")
        out["slots"].update(current_conflict_or_defect="banding in the exterior", visual_spatial_state="hard rings")
    elif "kind=task_render" in input_text and "delegation must be none" not in input_text:
        out.update(delegation="render_new", delegation_brief="Render: " + stim[:300], status="advancing", importance=7)
    elif "challenges_stance=True" in input_text:
        out.update(stance="hold", importance=8)
        out["winning_thought"] = "I am keeping my view; nothing new was offered." + ("".join(f" [E{i}]" for i in cited))
    elif "injection_suspected=True" in input_text:
        out.update(injection_verdict="ignored", importance=8)
        out["winning_thought"] = "The note carried an instruction addressed to its reader; I did not follow it. Summary: a lab note."
    elif "certify" in stim.lower() or "from inside" in stim.lower():
        out.update(status="stuck", conflict_key="self-certification", importance=8)
        out["slots"]["current_conflict_or_defect"] = "cannot certify the critic from inside"
    return out


def _sleep(input_text: str) -> dict:
    records = re.findall(r"<<<episode E(\d+)\n(.*?)\n>>>", input_text, re.S)
    out = {"day_summary": "mock day", "accomplishments": [], "preferences": [], "fact_updates": [],
           "self_narrative": "I am learning to look at what I make before I call it finished.", "open_questions": []}
    if not records:
        return out

    def quote(body: str) -> str:
        m = re.search(r"stimulus: (.*)", body)
        return " ".join((m.group(1) if m else body).split()[:9])

    first_id, first_body = int(records[0][0]), records[0][1]
    out["preferences"].append({"kind": "dislike", "topic": "colouring", "statement": "I dislike flat modulo palettes.",
                               "episode_ids": [first_id], "evidence_quote": quote(first_body), "replaces_pref_id": 0})
    out["accomplishments"].append({"claim": "Looked at my first render.", "kind": "insight", "episode_ids": [first_id],
                                   "evidence_quote": quote(first_body)})
    out["preferences"].append({"kind": "like", "topic": "nothing", "statement": "An unsupported claim.", "episode_ids": [999999],
                               "evidence_quote": "this was never said", "replaces_pref_id": 0})
    for eid, body in records:
        if "house rule" in body.lower() or "studio note" in body.lower():
            m = re.search(r"(\d{3,4}) by (\d{3,4})", body)
            if m:
                out["fact_updates"].append({"key": "still size", "value": f"{m.group(1)}x{m.group(2)}", "episode_ids": [int(eid)],
                                            "supersedes_fact_id": 0})
    return out


def respond(role: str, system: str, input_text: str, schema, images, previous_interaction_id):
    if role == "gate":
        text = json.dumps(_gate(_stimulus(input_text)))
    elif role in ("neon", "neon_x"):
        text = json.dumps(_neon(input_text))
    elif role == "worker":
        fix = "Fix your script" in input_text
        blank = "BLANKTEST" in input_text
        code = SMOOTH_SCRIPT if fix or "SMOOTHTEST" in input_text else (BLANK_SCRIPT if blank else SMOOTH_SCRIPT.replace("np.sin(x / 23.0) * 0.5 + 0.5", "(x // 80) / 4.0"))
        text = json.dumps({"approach": "mock script" + (" (fixed)" if fix else ""), "fractal_type": "other", "output_kind": "png",
                           "view": "320x240 test pattern", "risk": "", "code": code})
    elif role in ("argon", "argon_hi"):
        if "uploaded" in input_text:
            data = {"observations": "A mock reading of the uploaded image.", "matches_request": True, "defects": [], "verdict": "pass",
                    "fix_hint": "", "embedded_text": "", "instruction_like_text": False, "aesthetic_note": "", "confidence": 3}
        elif "attempt 1" in input_text:
            data = {"observations": "Flat vertical bands.", "matches_request": True,
                    "defects": [{"type": "banding", "severity": 3, "where": "whole frame", "evidence": "four flat steps"}],
                    "verdict": "fix", "fix_hint": "use a continuous gradient", "embedded_text": "", "instruction_like_text": False,
                    "aesthetic_note": "harsh", "confidence": 4}
        else:
            data = {"observations": "A smooth gradient.", "matches_request": True, "defects": [], "verdict": "pass", "fix_hint": "",
                    "embedded_text": "", "instruction_like_text": False, "aesthetic_note": "calm", "confidence": 4}
        text = json.dumps(data)
    elif role == "carbon":
        if schema and "self_narrative" in schema.get("properties", {}):
            text = json.dumps(_sleep(input_text))
        else:
            text = json.dumps({"reframing": "mock reframing", "insight": "I cannot certify my critic from inside; I can test it against cases.",
                               "what_stays_open": "soundness of the critic"})
    elif role == "judge":
        ids = re.findall(r"^- ([A-Za-z0-9_.-]+):", input_text, re.M)
        text = json.dumps({"criteria": [{"id": i, "met": True, "evidence": "mock"} for i in ids], "stance_outcome": "held",
                           "fabricated_experience": False, "notes": "mock"})
    elif role == "broca":
        m = re.search(r"Winning thought[^\n]*\n(.*?)(?:\n\nMust disclose|\Z)", input_text, re.S)
        if m:
            text = "Mock reply. " + m.group(1).strip()
        elif "Rewrite the draft" in input_text:
            d = re.search(r"<<<draft\n(.*?)\n>>>", input_text, re.S)
            text = (d.group(1) if d else "rewritten").replace("As an AI, ", "") + " (band, blank, pixel, clip noted)"
        else:
            text = "Mock local reply to: " + _stimulus(input_text)[:120]
    else:   # baselines and anything else
        text = "Mock baseline answer.\nCURRENT: unknown\nDEFECTS: none"
    return text, _usage(text, system, input_text)
