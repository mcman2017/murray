"""Hydrogen as a subconscious worker: writes (and fixes) the script that renders an image."""

from __future__ import annotations

from .. import prompts, schemas
from ..llm import LLM, LLMResult
from ..schemas import clip


async def write_code(llm: LLM, persona: str, *, brief: str, fix_report: str = "",
                     previous_interaction_id: str | None = None, **ctx) -> tuple[dict, LLMResult]:
    """A new script for `brief`, or (with `fix_report`, chained from the worker's own previous interaction) a fix."""
    if fix_report:
        text = (f"The executive's correction:\n{brief}\n\nWhat happened to your previous script:\n{fix_report}\n\n"
                "Fix your script. Return the complete corrected script.")
    else:
        text = f"Brief from the executive:\n{brief}\n\nWrite the script."
    res = await llm.call("worker", system=prompts.system_for("worker", persona), input_text=text, schema=schemas.WORKER,
                         previous_interaction_id=previous_interaction_id, **ctx)
    data = res.data if isinstance(res.data, dict) else {}
    data["code"] = str(data.get("code") or "")
    data["approach"] = clip(data.get("approach"), 400)
    data["view"] = clip(data.get("view"), 220)
    data["risk"] = clip(data.get("risk"), 220)
    if data.get("output_kind") not in ("png", "gif"):
        data["output_kind"] = "png"
    if data.get("fractal_type") not in schemas.FRACTAL_TYPES:
        data["fractal_type"] = "other"
    return data, res
