"""The single-model baselines: one raw Gemini 4 model, a static prompt, the raw transcript, zero-shot rendering.

No salience gate, no Global Workspace, no visual critic, no consolidated memory. The baseline gets the same persona
core and exactly the same render contract as Murray, so a difference in outcome is a difference in architecture.
A script the baseline writes is run once in the same sandbox and its image is delivered as it comes out.
"""

from __future__ import annotations

import asyncio
import re
import secrets
from pathlib import Path

from . import config, imaging, prompts
from .db import DB
from .llm import LLM
from .schemas import clip

CODE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)

RENDER_NOTE = ("\n\nIf the request needs an image, plot, fractal or animation, include exactly one complete Python script in a "
               "```python block that follows the render contract above. It will be run once, as written, and its image shown "
               "to the person. You will not see the image.")


def system_prompt(log_text: str = "") -> str:
    text = prompts.baseline_system() + RENDER_NOTE
    if log_text:
        text += ("\n\nYour raw activity log follows (oldest first). It is everything you have done; cite an entry as [E12] "
                 "only if it is in this log.\n\n" + log_text)
    return text


def transcript(messages: list[dict]) -> str:
    """The raw, linear transcript: every turn so far, then the message to answer."""
    lines = []
    for m in messages[:-1]:
        who = "Person" if m["role"] == "user" else "You"
        lines.append(f"{who}: {m['text']}")
    last = messages[-1]["text"]
    if lines:
        return "Conversation so far:\n" + "\n\n".join(lines) + f"\n\nPerson: {last}"
    return last


def raw_log(db: DB, limit_chars: int = 180000) -> str:
    """Murray's canonical episodic log as plain text: the same information, without the architecture."""
    rows = db.q("SELECT id, day, kind, stimulus, response, argon_json FROM episodes WHERE scope='canon' ORDER BY id")
    out = []
    for r in rows:
        line = f"[E{r['id']}] day {r['day']} ({r['kind']}): {clip(r['stimulus'], 900)}\n  you said: {clip(r['response'], 900)}"
        if r["argon_json"]:
            line += f"\n  critic's report: {clip(r['argon_json'], 500)}"
        out.append(line)
    text = "\n".join(out)
    return text[-limit_chars:]


async def answer(llm: LLM, sandbox, *, which: str, messages: list[dict], context: str, data_dir: Path | None = None,
                 log_text: str = "", scope: str | None = None, turn_id: int | None = None, run_render: bool = True) -> dict:
    """One baseline reply. `which` is hydrogen | neon | neon_log. Returns the reply, and the image if a script ran."""
    role = "base_h" if which == "hydrogen" else "base_n"
    last = messages[-1]
    images = [last["image_png"]] if last.get("image_png") else None
    res = await llm.call(role, system=system_prompt(log_text if which == "neon_log" else ""), input_text=transcript(messages),
                         images=images, context=context, scope=scope, turn_id=turn_id, store=False)
    reply = res.text.strip()
    out = {"reply": reply, "code": None, "image_path": None, "gif_path": None, "sandbox_status": None, "stats": None,
           "measured": [], "latency_ms": res.latency_ms, "cost_usd": res.cost_usd, "model": res.model, "level": "default",
           "tokens": {"in": res.input_tokens, "out": res.output_tokens, "thought": res.thought_tokens, "cached": res.cached_tokens}}
    m = CODE_RE.search(reply)
    if not (m and run_render):
        return out
    code = m.group(1)
    out["code"] = code
    frames = "frames/" in code
    sres = await sandbox.run(code, frames=frames)
    out["sandbox_status"] = sres.status
    out["sandbox_message"] = clip(sres.message, 600)
    if sres.files:
        out.update(await asyncio.to_thread(_store, sres.files, Path(data_dir or config.DATA_DIR) / "renders"))
    return out


def _store(files: list[tuple[str, bytes]], renders_dir: Path) -> dict:
    frames = []
    for _, data in files[: config.SANDBOX_MAX_FRAMES]:
        try:
            frames.append(imaging.decode_png(data))
        except imaging.ImageRejected:
            continue
    if not frames:
        return {"sandbox_status": "no_output"}
    rel = "b" + secrets.token_hex(8)
    base = renders_dir / rel
    base.mkdir(parents=True, exist_ok=True)
    out: dict = {"n_frames": len(frames)}
    if len(frames) == 1:
        st = imaging.stats(frames[0])
        imaging.save_png(frames[0], base / "it1.png")
        out.update({"image_path": f"{rel}/it1.png", "stats": st, "measured": imaging.detect(st)})
    else:
        idx = imaging.key_frames(len(frames))
        per = {i: imaging.stats(frames[i]) for i in idx}
        imaging.save_png(frames[len(frames) // 2], base / "it1.png")
        imaging.save_png(imaging.contact_sheet(frames, idx), base / "it1_sheet.png")
        imaging.save_gif(frames, base / "it1.gif")
        measured = {i: imaging.detect(per[i]) for i in idx}
        out.update({"image_path": f"{rel}/it1.png", "gif_path": f"{rel}/it1.gif", "sheet_path": f"{rel}/it1_sheet.png",
                    "stats": {"frames": {str(i): per[i] for i in idx}},
                    "measured": sorted({c for v in measured.values() for c in v}),
                    "measured_frames": {str(i): v for i, v in measured.items() if v}})
    return out
