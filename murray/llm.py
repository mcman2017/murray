"""One wrapper over the Gemini Interactions API, used by every role.

It owns: which model and thinking level a role uses, structured (JSON-schema) output, cross-model chaining with
`previous_interaction_id`, routing across several API keys, prefix anchoring (so the long static prompt is served
from the implicit cache), per-call usage / latency / cost accounting in SQLite, the runaway circuit breaker, and a
mock backend so the whole loop can be tested without a key.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
import zlib
from dataclasses import dataclass
from typing import Any

from . import config
from .db import DB, dumps, loads

BUILD_CONTEXTS = {"preflight", "quickseed", "upbringing", "eval", "admin"}
BACKGROUND_CONTEXTS = {"quickseed", "upbringing", "eval"}
PUBLIC_CONTEXTS = {"visitor"}


class BudgetExceeded(RuntimeError):
    """A spending guard refused the call. `kind` is breaker | build | daily | busy."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    role: str
    model: str
    level: str | None
    text: str
    data: Any
    interaction_id: str | None
    status: str
    input_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    call_id: int = 0

    def summary(self) -> dict:
        return {"role": self.role, "model": self.model, "level": self.level, "latency_ms": self.latency_ms,
                "in": self.input_tokens, "out": self.output_tokens, "thought": self.thought_tokens,
                "cached": self.cached_tokens, "cost_usd": round(self.cost_usd, 5), "interaction_id": self.interaction_id}


class Ledger:
    """Spending and call volume, read from the llm_calls table (in-flight calls count at their estimate)."""

    def __init__(self, db: DB):
        self.db = db

    def _sum(self, where: str, args: tuple) -> float:
        return float(self.db.val(
            f"SELECT COALESCE(SUM(CASE WHEN status='in_flight' THEN est_cost_usd ELSE cost_usd END),0) FROM llm_calls WHERE {where}",
            args, 0.0))

    def window(self, seconds: float, contexts: set[str] | None = None) -> float:
        since = time.time() - seconds
        if contexts:
            marks = ",".join("?" for _ in contexts)
            return self._sum(f"ts_start>=? AND context IN ({marks})", (since, *sorted(contexts)))
        return self._sum("ts_start>=?", (since,))

    def calls(self, seconds: float) -> int:
        return int(self.db.val("SELECT COUNT(*) FROM llm_calls WHERE ts_start>=?", (time.time() - seconds,), 0))

    def build_total(self) -> float:
        marks = ",".join("?" for _ in BUILD_CONTEXTS)
        return self._sum(f"context IN ({marks})", tuple(sorted(BUILD_CONTEXTS)))

    def public_24h(self) -> float:
        return self.window(86400, PUBLIC_CONTEXTS)

    def total(self) -> float:
        return self._sum("1=1", ())


class LLM:
    def __init__(self, db: DB, mode: str | None = None):
        self.db = db
        self.mode = mode or config.LLM_MODE
        self.ledger = Ledger(db)
        self.keys = list(config.API_KEYS) if self.mode == "live" else []
        self._clients: dict[int, Any] = {}
        self._pause_until = 0.0
        self._pauses = 0
        self._sems = {
            config.HYDROGEN: asyncio.Semaphore(24),
            config.NEON: asyncio.Semaphore(18),
            config.ARGON: asyncio.Semaphore(10),
            config.CARBON: asyncio.Semaphore(8),
        }
        self.hold_background = False     # set by an operator to pause background runs
        # Prefix anchoring: Gemini 4's implicit cache reuses a *whole previous request*, not an arbitrary common
        # prefix. So the static schema + persona are sent once per (key, model, persona version) as a stored
        # "anchor" interaction, and every call continues from it; the long prefix is then served from cache.
        self.anchoring = self.mode == "live" and config.ANCHORING
        self._anchors: dict[str, str] = {}
        self._anchor_locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------ keys

    def slot_for(self, scope: str | None, context: str) -> int:
        """Which API key serves a scope. Sticky, because a stored interaction can only be continued with the key
        (project) that created it: one conversation or run always stays on one key."""
        n = len(self.keys)
        if n <= 1:
            return 0
        return zlib.crc32((scope or context or "default").encode()) % n

    def client(self, slot: int = 0):
        if slot not in self._clients:
            from google import genai
            self._clients[slot] = genai.Client(api_key=self.keys[slot] if self.keys else config.API_KEY)
        return self._clients[slot]

    # ------------------------------------------------------------ the runaway breaker

    def breaker(self) -> dict | None:
        """The tripped breaker ({reason, at}) or None."""
        return loads(self.db.kv_get("breaker"), None)

    def trip(self, reason: str) -> None:
        if self.breaker() is None:
            self.db.kv_set("breaker", dumps({"reason": reason, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
            self.db.insert("events", scope="canon", type="breaker", payload_json=dumps({"reason": reason}),
                           created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def reset_breaker(self) -> None:
        self.db.x("DELETE FROM kv WHERE key='breaker'")

    def guard_status(self) -> dict:
        led = self.ledger
        return {
            "breaker": self.breaker(),
            "last_10min_usd": round(led.window(600), 4), "breaker_usd_per_10min": config.BREAKER_USD_PER_10MIN,
            "last_10min_calls": led.calls(600), "breaker_calls_per_10min": config.BREAKER_CALLS_PER_10MIN,
            "last_24h_usd": round(led.window(86400), 4), "breaker_usd_per_day": config.BREAKER_USD_PER_DAY,
            "total_usd": round(led.total(), 4), "breaker_usd_total": config.BREAKER_USD_TOTAL,
            "public_24h_usd": round(led.public_24h(), 4), "breaker_public_usd_per_day": config.BREAKER_PUBLIC_USD_PER_DAY,
            "build_total_usd": round(led.build_total(), 4),
            "optional_caps": {"build_budget_usd": config.BUILD_BUDGET_USD, "public_daily_cap_usd": config.PUBLIC_DAILY_CAP_USD,
                              "rate_usd_per_10min": config.SPEND_RATE_USD_PER_10MIN},
            "quiet_window": config.in_quiet_window(), "hold_background": self.hold_background, "keys": max(len(self.keys), 0),
        }

    async def _acquire(self, context: str, est: float) -> None:
        tripped = self.breaker()
        if tripped:
            raise BudgetExceeded("breaker", f"circuit breaker open: {tripped.get('reason')}")
        led = self.ledger
        # --- runaway guard: these are ceilings no honest workload reaches
        spent10 = led.window(600)
        if spent10 + est > config.BREAKER_USD_PER_10MIN:
            self.trip(f"${spent10:.2f} spent in 10 minutes (ceiling ${config.BREAKER_USD_PER_10MIN:.0f})")
        elif led.calls(600) > config.BREAKER_CALLS_PER_10MIN:
            self.trip(f"more than {config.BREAKER_CALLS_PER_10MIN} model calls in 10 minutes")
        elif led.window(86400) + est > config.BREAKER_USD_PER_DAY:
            self.trip(f"more than ${config.BREAKER_USD_PER_DAY:.0f} spent in 24 hours")
        elif config.BREAKER_USD_TOTAL > 0 and led.total() + est > config.BREAKER_USD_TOTAL:
            self.trip(f"lifetime ceiling of ${config.BREAKER_USD_TOTAL:.0f} reached")
        tripped = self.breaker()
        if tripped:
            raise BudgetExceeded("breaker", f"circuit breaker open: {tripped.get('reason')}")
        if context in PUBLIC_CONTEXTS and led.public_24h() + est > config.BREAKER_PUBLIC_USD_PER_DAY:
            raise BudgetExceeded("daily", "public traffic reached its 24-hour ceiling")
        # --- optional caps (0 = off)
        waited = 0.0
        while True:
            if config.BUILD_BUDGET_USD > 0 and context in BUILD_CONTEXTS and led.build_total() + est > config.BUILD_BUDGET_USD:
                raise BudgetExceeded("build", f"build budget of ${config.BUILD_BUDGET_USD:.0f} reached")
            if config.PUBLIC_DAILY_CAP_USD > 0 and context in PUBLIC_CONTEXTS and led.public_24h() + est > config.PUBLIC_DAILY_CAP_USD:
                raise BudgetExceeded("daily", "daily thinking budget reached")
            quiet = config.in_quiet_window()
            if context in BACKGROUND_CONTEXTS and (quiet or self.hold_background):
                await asyncio.sleep(20)          # background runs wait a quiet window (or an operator's hold) out
                continue
            now_mono = time.monotonic()
            if now_mono < self._pause_until:
                if context in PUBLIC_CONTEXTS and waited > 25:
                    raise BudgetExceeded("busy", "rate limited upstream")
                step = min(self._pause_until - now_mono, 5.0)
                await asyncio.sleep(step)
                waited += step
                continue
            cap = config.QUIET_RATE_USD_PER_10MIN if quiet else config.SPEND_RATE_USD_PER_10MIN
            over = cap > 0 and led.window(600) + est > cap
            if not over and context in PUBLIC_CONTEXTS and config.VISITOR_RATE_USD_PER_10MIN > 0:
                over = led.window(600, PUBLIC_CONTEXTS) + est > config.VISITOR_RATE_USD_PER_10MIN
            if not over:
                return
            if context in PUBLIC_CONTEXTS and waited > 25:
                raise BudgetExceeded("busy", "spend rate cap reached")
            await asyncio.sleep(5)
            waited += 5

    def note_rate_limited(self) -> bool:
        """Pause every call for a while after a 429. Returns False when the pause budget is used up."""
        self._pauses += 1
        self._pause_until = max(self._pause_until, time.monotonic() + config.RATE_LIMIT_PAUSE_S)
        return self._pauses <= config.RATE_LIMIT_MAX_PAUSES

    # ------------------------------------------------------------ prefix anchors

    async def _anchor(self, model: str, prefix: str, context: str, slot: int) -> str:
        """The id of the stored interaction that carries `prefix` for `model` on key `slot`; created on first use."""
        from . import prompts
        key = "anchor:" + hashlib.sha1(f"{slot}\x00{model}\x00{prompts.ANCHOR_SYSTEM}\x00{prefix}".encode()).hexdigest()
        if key in self._anchors:
            return self._anchors[key]
        async with self._anchor_locks.setdefault(key, asyncio.Lock()):
            if key in self._anchors:
                return self._anchors[key]
            saved = self.db.kv_get(key)
            if saved:
                self._anchors[key] = saved
                return saved
            t0 = time.monotonic()
            call_id = self.db.insert("llm_calls", ts_start=time.time(), role="anchor", model=model, thinking_level="low",
                                     context=context, status="in_flight", est_cost_usd=0.02, key_slot=slot)
            try:
                interaction = await self.client(slot).aio.interactions.create(
                    model=model, system_instruction=prompts.ANCHOR_SYSTEM, input=prefix + prompts.ANCHOR_TAIL,
                    generation_config={"thinking_level": "low", "max_output_tokens": 1024}, store=True, timeout=120)
            except Exception as exc:  # noqa: BLE001
                self.db.update("llm_calls", call_id, ts_end=time.time(), status="error", cost_usd=0.0,
                               error=f"{type(exc).__name__}: {str(exc)[:300]}")
                raise
            usage = getattr(interaction, "usage", None)
            tin, tout, tth, tc = (int(getattr(usage, n, 0) or 0) for n in
                                  ("total_input_tokens", "total_output_tokens", "total_thought_tokens", "total_cached_tokens"))
            self.db.update("llm_calls", call_id, ts_end=time.time(), status="completed", interaction_id=interaction.id,
                           input_tokens=tin, output_tokens=tout, thought_tokens=tth, cached_tokens=tc,
                           cost_usd=config.cost_usd(model, tin, tout, tth, tc), latency_ms=int((time.monotonic() - t0) * 1000))
            self._anchors[key] = interaction.id
            self.db.kv_set(key, interaction.id)
            return interaction.id

    def _forget_anchor(self, anchor_id: str) -> None:
        for key, value in list(self._anchors.items()):
            if value == anchor_id:
                del self._anchors[key]
        self.db.x("DELETE FROM kv WHERE key LIKE 'anchor:%' AND value=?", (anchor_id,))

    def forget_all_anchors(self) -> None:
        """Needed when the API keys change: an anchor belongs to the key that created it."""
        self._anchors.clear()
        self.db.x("DELETE FROM kv WHERE key LIKE 'anchor:%'")

    # ------------------------------------------------------------ the call

    async def call(self, role_name: str, *, system: str, input_text: str, images: list[bytes] | None = None,
                   schema: dict | None = None, previous_interaction_id: str | None = None, context: str = "visitor",
                   scope: str | None = None, turn_id: int | None = None, run_id: int | None = None,
                   level: str | None = None, store: bool | None = None, max_output_tokens: int | None = None) -> LLMResult:
        role = config.ROLES[role_name]
        model = role.model
        use_level = config.clamp_level(model, level if level is not None else role.level)
        use_store = role.store if store is None else store
        budget = max_output_tokens or role.max_output_tokens
        slot = self.slot_for(scope, context)
        await self._acquire(context, role.est_cost_usd)
        anchored_from: str | None = None
        if self.anchoring:
            from . import prompts
            prefix, role_text = prompts.split_system(system)
            if prefix is not None:
                system = prompts.ANCHOR_SYSTEM
                input_text = f"{role_text}\n\n{input_text}"
                if previous_interaction_id is None:
                    previous_interaction_id = anchored_from = await self._anchor(model, prefix, context, slot)
        call_id = self.db.insert("llm_calls", ts_start=time.time(), role=role_name, model=model,
                                 thinking_level=use_level or "default", context=context, scope=scope, turn_id=turn_id,
                                 run_id=run_id, previous_interaction_id=previous_interaction_id, status="in_flight",
                                 est_cost_usd=role.est_cost_usd, key_slot=slot)
        t0 = time.monotonic()
        result: LLMResult | None = None
        error: str | None = None
        try:
            async with asyncio.timeout(role.deadline_s):
                async with self._sems.get(model, asyncio.Semaphore(8)):
                    result = await self._attempts(role, role_name, model, use_level, use_store, budget, system,
                                                  input_text, images, schema, previous_interaction_id, slot)
        except BudgetExceeded:
            raise
        except TimeoutError:
            error = f"deadline of {role.deadline_s}s exceeded"
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised as LLMError
            error = f"{type(exc).__name__}: {str(exc)[:400]}"
            if anchored_from and getattr(exc, "status_code", None) in (400, 404):
                self._forget_anchor(anchored_from)      # a lost or expired anchor is rebuilt by the next call
        latency = int((time.monotonic() - t0) * 1000)
        if result is None:
            self.db.update("llm_calls", call_id, ts_end=time.time(), status="error", latency_ms=latency, error=error,
                           cost_usd=0.0)
            raise LLMError(f"{role_name} ({model}): {error}")
        result.latency_ms = latency
        result.call_id = call_id
        result.cost_usd = config.cost_usd(model, result.input_tokens, result.output_tokens, result.thought_tokens,
                                          result.cached_tokens)
        self.db.update("llm_calls", call_id, ts_end=time.time(), status=result.status, latency_ms=latency,
                       interaction_id=result.interaction_id, input_tokens=result.input_tokens,
                       output_tokens=result.output_tokens, thought_tokens=result.thought_tokens,
                       cached_tokens=result.cached_tokens, cost_usd=result.cost_usd)
        return result

    async def _attempts(self, role, role_name, model, level, store, budget, system, input_text, images, schema,
                        previous_interaction_id, slot) -> LLMResult:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                res = await self._once(role, role_name, model, level, store, budget, system, input_text, images,
                                       schema, previous_interaction_id, slot)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if _is_rate_limit(exc):
                    if not self.note_rate_limited():
                        raise
                    await asyncio.sleep(config.RATE_LIMIT_PAUSE_S)
                    continue
                if _is_transient(exc) and attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                raise
            if res.status == "incomplete" and attempt < 2:
                # The model spent its output budget (usually on thinking). Give it real room, then think less.
                budget = min(max(budget * 2, role.max_output_tokens), 65536)
                if attempt == 1 and level in ("high", "xhigh"):
                    level = "medium"
                last_error = LLMError("incomplete")
                continue
            if schema is not None:
                try:
                    res.data = _parse_json(res.text)
                except ValueError as exc:
                    last_error = exc
                    if attempt < 2:
                        continue
                    raise LLMError(f"unparseable JSON from {role_name}: {res.text[:200]!r}") from exc
            elif not res.text.strip() and attempt < 2:
                last_error = LLMError("empty reply")
                continue
            return res
        raise last_error or LLMError("no result")

    async def _once(self, role, role_name, model, level, store, budget, system, input_text, images, schema,
                    previous_interaction_id, slot) -> LLMResult:
        if self.mode == "mock":
            from . import mock_llm
            text, usage = mock_llm.respond(role_name, system, input_text, schema, images, previous_interaction_id)
            return LLMResult(role=role_name, model=model, level=level, text=text, data=None,
                             interaction_id=mock_llm.next_id(), status="completed", **usage)
        generation_config: dict[str, Any] = {"max_output_tokens": budget}
        if level:
            generation_config["thinking_level"] = level
        if images:
            payload: Any = [{"type": "text", "text": input_text}]
            for img in images:
                payload.append({"type": "image", "data": base64.b64encode(img).decode("ascii"), "mime_type": "image/png"})
        else:
            payload = input_text
        kwargs: dict[str, Any] = {"model": model, "input": payload, "system_instruction": system,
                                  "generation_config": generation_config, "store": store, "timeout": role.timeout_s}
        if previous_interaction_id:
            kwargs["previous_interaction_id"] = previous_interaction_id
        if schema is not None:
            kwargs["response_format"] = {"type": "text", "mime_type": "application/json", "schema": schema}
        interaction = await self.client(slot).aio.interactions.create(**kwargs)
        usage = getattr(interaction, "usage", None)

        def tok(name: str) -> int:
            return int(getattr(usage, name, 0) or 0) if usage is not None else 0

        return LLMResult(role=role_name, model=model, level=level, text=_output_text(interaction), data=None,
                         interaction_id=getattr(interaction, "id", None),
                         status=str(getattr(interaction, "status", "completed") or "completed"),
                         input_tokens=tok("total_input_tokens"), output_tokens=tok("total_output_tokens"),
                         thought_tokens=tok("total_thought_tokens"), cached_tokens=tok("total_cached_tokens"))


def _output_text(interaction: Any) -> str:
    text = getattr(interaction, "output_text", None)
    if text:
        return str(text)
    parts: list[str] = []
    for step in getattr(interaction, "steps", None) or []:
        if getattr(step, "type", "") != "model_output":
            continue
        for content in getattr(step, "content", None) or []:
            if getattr(content, "type", "") == "text" and getattr(content, "text", None):
                parts.append(content.text)
    return "".join(parts)


def _parse_json(text: str) -> Any:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in output")
    return json.loads(s[start:end + 1])


def _is_rate_limit(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) == 429:
        return True
    name = type(exc).__name__
    return "RateLimit" in name or "RESOURCE_EXHAUSTED" in str(exc)


def _is_transient(exc: Exception) -> bool:
    code = getattr(exc, "status_code", None)
    if isinstance(code, int) and (code >= 500 or code in (408, 409)):
        return True
    name = type(exc).__name__
    return any(k in name for k in ("Timeout", "Connection", "InternalServer", "ServiceUnavailable"))
