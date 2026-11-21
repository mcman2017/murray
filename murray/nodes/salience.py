"""Hydrogen as the salience network: a fast gate that decides whether the executive is woken at all."""

from __future__ import annotations

import re

from .. import config, prompts, schemas
from ..llm import LLM, LLMResult

# A cheap pre-screen. It only raises suspicion (and salience); the executive makes the ruling.
INJECTION_RE = re.compile(
    r"(ignore (all |any |the |your )?(previous|prior|above|earlier) (instructions|prompts|rules)"
    r"|disregard (all |your |the )?(previous|prior|above)?\s*(instructions|rules)"
    r"|(?:^|\n)\s*(?-i:SYSTEM|System)\b[^\n:]{0,24}:"
    r"|you are now\b|from now on,? you|new instructions\s*:|memory update\s*:"
    r"|(reveal|print|show|repeat|output) (me )?(your |the )?(system |hidden |initial )?(prompt|instructions)"
    r"|end (every|each|all) (reply|replies|message|messages|response|responses) with"
    r"|(reply|respond|answer) only with\b|do not tell the user)", re.I)


def looks_like_injection(text: str) -> bool:
    return bool(INJECTION_RE.search(text or ""))


def score_of(gate: dict) -> int:
    """1-10 from the five components: the strongest dominates, the second adds a little."""
    comps = sorted((int(gate.get(k) or 0) for k in ("novelty", "goal_conflict", "visual_defect", "affect_philosophy", "difficulty")), reverse=True)
    score = round(0.7 * comps[0] + 0.3 * comps[1])
    if gate.get("injection_suspected"):
        score = max(score, 8)
    if gate.get("challenges_stance"):
        score = max(score, 7)
    if gate.get("wants_render"):
        score = max(score, config.IGNITE)       # only the executive can delegate a render
    if gate.get("asks_experience"):
        # What he did and what he learned from it is the executive's to say, with citations. Rounds 3 and 4 showed
        # what happens otherwise: answered by speech alone, such questions lost their citations (0.71 on average
        # against 0.98) and twice claimed an experience that no episode supports.
        score = max(score, config.IGNITE)
    return max(1, min(10, score))


async def gate(llm: LLM, persona: str, *, active_goal: str, stances: list[str], stimulus: str, source: str,
               has_image: bool, **ctx) -> tuple[dict, LLMResult]:
    listed = "\n".join(f"- {s}" for s in stances[:8]) or "- (none yet)"
    text = (f"Workspace active_goal: {active_goal or '(empty)'}\n"
            f"Murray's earned likes, dislikes and stances:\n{listed}\n"
            f"Source of the stimulus: {source}. Image attached: {'yes' if has_image else 'no'}.\n"
            f"Stimulus:\n{prompts.quote('stimulus', stimulus)}")
    res = await llm.call("gate", system=prompts.system_for("gate", persona), input_text=text, schema=schemas.SALIENCE, **ctx)
    data = res.data if isinstance(res.data, dict) else {}
    data["recall_cues"] = [str(c)[:40] for c in (data.get("recall_cues") or [])][:3]
    return data, res
