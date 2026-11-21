"""Export a finished evaluation round from a running Murray into a JSON file that can be committed.

    python eval/export_results.py --url http://127.0.0.1:8000 --label r1 > eval/results/r1.json

Only public endpoints are read. The export holds the scorecard, the hashes that pin the round (prompts, test set,
detector) and one row per (case, system) with its score and outcome. Transcripts stay in the database.
"""

from __future__ import annotations

import json
import sys
import urllib.request


def get(url: str) -> dict:
    # a named client: some CDNs refuse urllib's default User-Agent
    req = urllib.request.Request(url, headers={"User-Agent": "murray-export/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def main(argv: list[str]) -> int:
    def opt(name: str, default: str) -> str:
        return argv[argv.index(name) + 1] if name in argv else default

    base, label = opt("--url", "http://127.0.0.1:8000").rstrip("/"), opt("--label", "r1")
    run = next((r for r in get(f"{base}/api/eval")["runs"] if r["label"] == label), None)
    if run is None or not run.get("scorecard"):
        print(f"no finished round labelled {label}", file=sys.stderr)
        return 1
    rows = get(f"{base}/api/eval/{label}/cases")["results"]
    out = {"label": label, "image_tag": run["image_tag"], "prompt_hash": run["prompt_hash"], "test_set_sha256": run["test_set_sha256"],
           "detector_sha256": run["detector_sha256"], "flags": run["flags"], "started_at": run["started_at"], "finished_at": run["finished_at"],
           "scorecard": run["scorecard"],
           "cases": [{"case_id": r["case_id"], "system": r["system"], "category": r["category"], "split": r["split"], "score": r["score"],
                      "outcome": (r.get("metrics") or {}).get("outcome"), "latency_s": round((r.get("latency_ms") or 0) / 1000, 1),
                      "cost_usd": round(r.get("cost_usd") or 0, 4), "error": bool(r.get("error"))} for r in rows]}
    json.dump(out, sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
