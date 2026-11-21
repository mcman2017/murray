"""Entry point: `python -m murray` runs the role named by MURRAY_ROLE (app | sandbox)."""

from __future__ import annotations

import sys

import uvicorn

from . import config


def main() -> int:
    if config.ROLE == "sandbox":
        if config.API_KEY:
            print("refusing to start: the sandbox role must never hold an API key", file=sys.stderr)
            return 2
        uvicorn.run("murray.sandbox.server:app", host=config.HOST, port=config.SANDBOX_PORT, log_level="warning")
        return 0
    if config.LLM_MODE == "live" and not config.SANDBOX_URL:
        print("refusing to start: with a live API key, model-written code must run in the separate sandbox "
              "service (set MURRAY_SANDBOX_URL)", file=sys.stderr)
        return 2
    uvicorn.run("murray.api:app", host=config.HOST, port=config.PORT, log_level="info", proxy_headers=True,
                forwarded_allow_ips="*", timeout_graceful_shutdown=20)
    return 0


if __name__ == "__main__":
    sys.exit(main())
