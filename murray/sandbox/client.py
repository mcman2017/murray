"""How the app reaches the sandbox. With a live API key the sandbox is always the remote service."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

import httpx

from .. import config


@dataclass
class SandboxResult:
    status: str                      # ok | policy | error | timeout | no_output | unreachable
    error: str = ""
    stderr_tail: str = ""
    stdout_tail: str = ""
    wall_ms: int = 0
    files: list[tuple[str, bytes]] = field(default_factory=list)   # (name, raw bytes) - untrusted

    @property
    def message(self) -> str:
        """What the model is told when a run did not produce an image."""
        parts = [self.error] if self.error else []
        if self.stderr_tail.strip():
            parts.append("stderr: " + self.stderr_tail.strip()[-900:])
        return " | ".join(parts)


def _from_json(data: dict) -> SandboxResult:
    files = []
    for f in data.get("files") or []:
        try:
            files.append((str(f["name"]), base64.b64decode(f["b64"], validate=True)))
        except Exception:  # noqa: BLE001 - a malformed entry is dropped
            continue
    return SandboxResult(status=str(data.get("status", "error")), error=str(data.get("error", "")),
                         stderr_tail=str(data.get("stderr_tail", "")), stdout_tail=str(data.get("stdout_tail", "")),
                         wall_ms=int(data.get("wall_ms") or 0), files=files)


class RemoteSandbox:
    def __init__(self, url: str):
        self.url = url.rstrip("/")

    async def run(self, code: str, frames: bool = False) -> SandboxResult:
        timeout = (config.SANDBOX_WALL_FRAMES_S if frames else config.SANDBOX_WALL_S) + 45
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{self.url}/run", json={"code": code[: config.SANDBOX_MAX_CODE_CHARS], "frames": frames})
                resp.raise_for_status()
                return _from_json(resp.json())
        except Exception as exc:  # noqa: BLE001
            return SandboxResult(status="unreachable", error=f"sandbox unavailable: {type(exc).__name__}")

    async def healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                return (await client.get(f"{self.url}/healthz")).status_code == 200
        except Exception:  # noqa: BLE001
            return False


class LocalSandbox:
    """In-process runner. Tests and mock mode only: never beside an API key (see murray.__main__)."""

    async def run(self, code: str, frames: bool = False) -> SandboxResult:
        from . import runner
        return _from_json(await runner.run_job(code, frames))

    async def healthy(self) -> bool:
        return True


def get_sandbox():
    if config.SANDBOX_URL:
        return RemoteSandbox(config.SANDBOX_URL)
    if config.LLM_MODE == "live":
        raise RuntimeError("a live API key requires the remote sandbox (MURRAY_SANDBOX_URL)")
    return LocalSandbox()
