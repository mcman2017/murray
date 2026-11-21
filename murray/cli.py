"""Operator commands, run inside the app container: they talk to the running server on localhost.

    python -m murray.cli status
    python -m murray.cli upbringing --quick | --full [--no-gate]
    python -m murray.cli stop
    python -m murray.cli reset
    python -m murray.cli eval --label r1 [--systems murray,baseline_hydrogen] [--cases A01,B05] [--hints] [--concurrency 3]
    python -m murray.cli hold on|off
    python -m murray.cli sleep
    python -m murray.cli usage [--minutes 60] [--json]      token, call and cost monitoring
    python -m murray.cli cost MODEL --in N [--cached N] [--out N] [--thought N] [--calls N]
    python -m murray.cli breaker [reset | trip "reason"]     the runaway guard
    python -m murray.cli sql "SELECT ..."        (read-only)
"""

from __future__ import annotations

import json
import sqlite3
import sys
import urllib.error
import urllib.request

from . import config


def _call(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{config.PORT}{path}", method=method,
                                 data=json.dumps(body or {}).encode() if method == "POST" else None,
                                 headers={"Authorization": f"Bearer {config.ADMIN_TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return {"http_error": exc.code, "detail": exc.read().decode("utf-8", "replace")[:500]}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]

    def opt(name: str, default: str | None = None) -> str | None:
        return rest[rest.index(name) + 1] if name in rest and rest.index(name) + 1 < len(rest) else default

    if cmd == "status":
        out = _call("GET", "/api/admin/status")
    elif cmd == "upbringing":
        out = _call("POST", "/api/admin/upbringing", {"mode": "quick" if "--quick" in rest else "full", "no_gate": "--no-gate" in rest})
    elif cmd == "failures":
        out = _call("POST", "/api/admin/failures")
    elif cmd == "stop":
        out = _call("POST", "/api/admin/stop")
    elif cmd == "reset":
        out = _call("POST", "/api/admin/reset")
    elif cmd == "sleep":
        out = _call("POST", "/api/admin/sleep")
    elif cmd == "hold":
        out = _call("POST", "/api/admin/hold", {"on": rest[:1] == ["on"]})
    elif cmd == "eval":
        body: dict = {"label": opt("--label", "r1"), "argon_hints": "--hints" in rest, "concurrency": int(opt("--concurrency", "3") or 3)}
        if opt("--systems"):
            body["systems"] = opt("--systems").split(",")
        if opt("--cases"):
            body["cases"] = opt("--cases").split(",")
        if opt("--tag"):
            body["image_tag"] = opt("--tag")
        out = _call("POST", "/api/admin/eval", body)
    elif cmd == "usage":
        from . import costs
        path = "/api/admin/usage" + (f"?minutes={opt('--minutes')}" if opt("--minutes") else "")
        out = _call("GET", path)
        if "http_error" not in out and "--json" not in rest:
            print(costs.format_report(out, out.get("guards")))
            return 0
    elif cmd == "cost":
        from . import costs
        if not rest or rest[0].startswith("-"):
            print(json.dumps(costs.price_table(), indent=1))
            return 0
        one = costs.calc(rest[0], inp=int(opt("--in", "0")), out=int(opt("--out", "0")), thought=int(opt("--thought", "0")),
                         cached=int(opt("--cached", "0")))
        calls = int(opt("--calls", "1"))
        print(json.dumps({**one, "calls": calls, "total_usd": round(one["usd"] * calls, 4)}, indent=1))
        return 0
    elif cmd == "breaker":
        if rest[:1] == ["reset"]:
            out = _call("POST", "/api/admin/breaker", {})
        elif rest[:1] == ["trip"]:
            out = _call("POST", "/api/admin/breaker", {"trip": " ".join(rest[1:]) or "manual"})
        else:
            out = _call("GET", "/api/admin/status").get("ledger", {})
    elif cmd == "sql":
        conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(rest[0]).fetchmany(int(opt("--limit", "200") or 200))
        for r in rows:
            print(json.dumps({k: r[k] for k in r.keys()}, ensure_ascii=False, default=str))
        return 0
    else:
        print(__doc__)
        return 2
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 1 if "http_error" in out else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
