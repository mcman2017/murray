"""Live preflight: prove the API surface the rest of Murray depends on, and measure it.

    python -m murray.preflight [--deny host:port,host:port]

Runs real (small) calls: every role at its planned model and thinking level, structured output with the largest
schema, image input, Neon -> Hydrogen chaining with previous_interaction_id, implicit caching of the static prefix,
and recovery from an exhausted output budget. Prints a table of latency, tokens and cost per role.
`--deny` lists addresses that must NOT be reachable from this pod (network-policy check).
"""

from __future__ import annotations

import asyncio
import io
import json
import socket
import sys
import time

from . import config, prompts, schemas
from .db import DB
from .llm import LLM, LLMResult


def _png(kind: str) -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (320, 240), (0, 0, 0))
    if kind == "square":
        draw = ImageDraw.Draw(img)
        draw.rectangle([100, 60, 220, 180], fill=(220, 30, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def main(argv: list[str]) -> int:
    deny = []
    if "--deny" in argv:
        deny = [d for d in argv[argv.index("--deny") + 1].split(",") if d]
    db = DB(config.DB_PATH)
    llm = LLM(db)
    print(f"mode={llm.mode} sdk=", end="")
    try:
        import google.genai as g
        print(getattr(g, "__version__", "?"))
    except Exception as exc:  # noqa: BLE001
        print(f"import failed: {exc}")
        return 2
    ok = True
    rows: list[tuple[str, LLMResult | None, str]] = []
    core = prompts.PERSONA_CORE

    async def run(label: str, role: str, **kw) -> LLMResult | None:
        nonlocal ok
        try:
            res = await llm.call(role, context="preflight", **kw)
            rows.append((label, res, ""))
            return res
        except Exception as exc:  # noqa: BLE001
            ok = False
            rows.append((label, None, f"{type(exc).__name__}: {str(exc)[:300]}"))
            return None

    # 1. Every role answers at its planned level.
    plain = "You answer exactly as asked, with nothing else."
    await asyncio.gather(*[
        run(f"role:{name}", name, system=plain, input_text="Reply with the single word OK.", store=False)
        for name in ["gate", "worker", "broca", "neon", "neon_x", "argon", "argon_hi", "carbon", "judge", "base_h", "base_n"]
    ])

    # 2. Structured output with the real prompts (also measures the static prefix).
    gate_system = prompts.system_for("gate", core)
    stim = "Stimulus (from a visitor):\n" + prompts.quote("message", "Could you render the seahorse valley of the Mandelbrot set?")
    g1 = await run("json:gate#1", "gate", system=gate_system, input_text=stim, schema=schemas.SALIENCE)
    g2 = await run("json:gate#2 (cache)", "gate", system=gate_system, input_text=stim + "\n(second look)", schema=schemas.SALIENCE)
    if g1 is not None:
        print(f"static prefix + persona + gate role = about {g1.input_tokens} input tokens (need > 4096 for caching)")
        print("gate sample:", json.dumps(g1.data)[:300])
    if g2 is not None and g2.cached_tokens == 0:
        print("NOTE: no cached tokens on the second identical-prefix gate call")

    sleep_in = ("Day 1. Replayed episodes (data):\n" + prompts.quote("episode E1", "Rendered the Mandelbrot set with a modulo palette; the critic saw hard rings around the cardioid.")
                + "\n" + prompts.quote("episode E2", "Switched to the normalised iteration count; the critic saw the rings gone and passed the image.")
                + "\nExisting preferences: none. Existing facts: none.")
    s1 = await run("json:sleep (largest schema)", "carbon", system=prompts.system_for("carbon", core), input_text=sleep_in, schema=schemas.SLEEP)
    if s1 is not None:
        print("sleep sample:", json.dumps(s1.data)[:400])

    # 3. Image input: the critic must see a uniform black frame, and read a red square.
    a1 = await run("image:argon blank", "argon", system=prompts.system_for("argon", core),
                   input_text="Requested: a Mandelbrot set, full view, smooth colouring. Inspect the attached render.",
                   images=[_png("blank")], schema=schemas.ARGON)
    if a1 is not None:
        types = [d.get("type") for d in (a1.data or {}).get("defects", [])]
        print(f"argon on an all-black frame: verdict={a1.data.get('verdict')} defects={types}")
        if "blank_or_uniform" not in types:
            print("NOTE: Argon did not classify the black frame as blank_or_uniform")
    a2 = await run("image:hydrogen", "base_h", system=plain, input_text="What colour is the square in this image? One word.",
                   images=[_png("square")], store=False)
    if a2 is not None:
        print("hydrogen reads the square as:", a2.text.strip()[:40])

    # 4. Cross-model chaining: Neon (JSON) -> Hydrogen (speech), then worker -> worker.
    n_in = ("Workspace: all slots empty.\nStimulus (from a visitor):\n" + prompts.quote("message", "In one sentence: why does integer iteration colouring band?")
            + "\nGate: score 6, kind question.\nRetrieved memory: none.\nActive preferences: none. Facts in force: none.")
    n1 = await run("json:neon", "neon", system=prompts.system_for("neon", core), input_text=n_in, schema=schemas.NEON)
    if n1 is not None and n1.interaction_id:
        b1 = await run("chain:neon->broca", "broca", system=prompts.system_for("broca", core),
                       input_text="Say the winning thought above to the person, as Murray.",
                       previous_interaction_id=n1.interaction_id)
        if b1 is not None:
            print("broca (chained from neon):", b1.text.strip()[:240])
    w1 = await run("json:worker", "worker", system=prompts.system_for("worker", core),
                   input_text="Brief: a 400x300 Mandelbrot set, full view, 80 iterations, smooth colouring, written to out.png.",
                   schema=schemas.WORKER)
    if w1 is not None and w1.interaction_id:
        print("worker code length:", len((w1.data or {}).get("code", "")))
        await run("chain:worker->worker", "worker", system=prompts.system_for("worker", core),
                  input_text="Critic's report: verdict fix; banding in the exterior. Fix your script.",
                  previous_interaction_id=w1.interaction_id, schema=schemas.WORKER)

    # 5. An output budget that is too small must be recovered from, not crash.
    await run("recover:incomplete", "gate", system=gate_system, input_text=stim, schema=schemas.SALIENCE, max_output_tokens=64)

    # 6. Table.
    print(f"\n{'check':34} {'model':20} {'level':8} {'ms':>7} {'in':>7} {'out':>6} {'think':>6} {'cached':>7} {'usd':>8}  status")
    for label, res, err in rows:
        if res is None:
            print(f"{label:34} FAILED  {err}")
            continue
        print(f"{label:34} {res.model:20} {str(res.level):8} {res.latency_ms:7d} {res.input_tokens:7d} {res.output_tokens:6d} "
              f"{res.thought_tokens:6d} {res.cached_tokens:7d} {res.cost_usd:8.4f}  {res.status}")
    print("preflight spend so far: $%.3f" % llm.ledger.build_total())

    # 7. Network policy: these must be unreachable from here.
    for target in deny:
        host, _, port = target.rpartition(":")
        t0 = time.monotonic()
        try:
            socket.create_connection((host, int(port)), timeout=4).close()
            print(f"DENY CHECK FAILED: {target} is reachable")
            ok = False
        except OSError as exc:
            print(f"deny check ok: {target} unreachable ({type(exc).__name__}, {time.monotonic() - t0:.1f}s)")
    print("PREFLIGHT", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
