"""The sandbox service: a tiny HTTP wrapper around the runner. It holds no secrets and has no network egress."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from pydantic import BaseModel, Field

from . import policy, runner

app = FastAPI(title="Murray sandbox", docs_url=None, redoc_url=None, openapi_url=None)
_slots = asyncio.Semaphore(2)


class Job(BaseModel):
    code: str = Field(max_length=policy.MAX_CODE_CHARS)
    frames: bool = False


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.post("/run")
async def run(job: Job) -> dict:
    async with _slots:
        return await runner.run_job(job.code, job.frames)
