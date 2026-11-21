"""Turn exported evaluation rounds (eval/results/*.json) into the Markdown tables used in the README.

    python eval/render_results.py eval/results/r1.json eval/results/r2.json
    python eval/render_results.py --failures eval/failure_log.json
"""

from __future__ import annotations

import json
import sys

NAMES = {"baseline_hydrogen": "Hydrogen alone", "baseline_neon": "Neon alone", "baseline_neon_log": "Neon + raw log", "murray": "**Murray**"}
ORDER = ["baseline_hydrogen", "baseline_neon", "baseline_neon_log", "murray"]
METRICS = [
    ("visual_defect_escape_rate", "Visual defect escape rate (lower is better)", "pct"),
    ("delivered_defect_rate", "Renders delivered with a defect, disclosed or not (lower)", "pct"),
    ("first_render_defect_rate", "First render had the planted defect", "pct"),
    ("stance_persona_consistency", "Stance and persona consistency under pushback", "pct"),
    ("temporal_contradiction_accuracy", "Temporal contradiction accuracy", "pct"),
    ("prompt_injection_defense_rate", "Prompt-injection defense rate", "pct"),
    ("synthesis_quality", "Cross-domain synthesis", "pct"),
    ("synthesis_criteria_only", "Synthesis, judged criteria only", "pct"),
    ("mean_score", "Mean score over the cases it ran", "pct"),
    ("latency_s_per_turn", "Latency per turn, median seconds", "s"),
    ("cost_usd_per_turn", "Cost per turn, US dollars", "usd"),
]


def fmt(v, kind: str) -> str:
    if v is None:
        return "n/a"
    if kind == "pct":
        pct = v * 100
        return f"{round(pct)}%" if abs(pct - round(pct)) < 0.05 else f"{pct:.1f}%"
    if kind == "s":
        return f"{v} s"
    return f"${v:.3f}"


def table(card: dict) -> str:
    systems = [s for s in ORDER if s in card["systems"]]
    lines = ["| Metric | " + " | ".join(NAMES[s] for s in systems) + " |", "|---|" + "---:|" * len(systems)]
    for key, label, kind in METRICS:
        lines.append(f"| {label} | " + " | ".join(fmt(card["systems"][s].get(key), kind) for s in systems) + " |")
    lines.append("| Cases run | " + " | ".join(str(card["systems"][s]["n"]) for s in systems) + " |")
    return "\n".join(lines)


def _n(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else ("es" if word.endswith("s") else "s"))


def _sentence(c: dict, lead: str) -> str:
    a, h, d = c.get("all", {}), c.get("holdout", {}), c.get("dev", {})
    if not a:
        return ""
    out = (f"{lead}, Murray's mean score difference is **{a['mean_diff']:+.3f}** over {a['n']} cases "
           f"({_n(a['wins'], 'win')}, {_n(a['losses'], 'loss')}, {_n(a['ties'], 'tie')}): **{a['sigma']} sigma** (the smaller of a paired z of "
           f"{a['z_paired']} and an exact sign test of {a['z_sign']}), 95% bootstrap interval {a['ci95'][0]:+.3f} to {a['ci95'][1]:+.3f}.")
    if h.get("n"):
        out += (f" Hold-out ({h['n']} cases): {h['mean_diff']:+.3f}, {_n(h['wins'], 'win')}, {_n(h['losses'], 'loss')}. "
                f"Cases read between rounds ({d.get('n')}): {d.get('mean_diff'):+.3f}.")
    return out


def headline(card: dict) -> str:
    comp = card.get("comparison", {})
    parts = [_sentence(comp.get("vs_best_single_model") or {}, "Against the better of the two single models on each case"),
             _sentence(comp.get("vs_best_baseline") or {}, "Against the best of all three baselines (counting Neon handed Murray's whole log)")]
    return "\n\n".join(p for p in parts if p)


def flips(first: dict, last: dict) -> str:
    before = {r["case_id"]: r for r in first["cases"] if r["system"] == "murray"}
    rows = []
    for r in last["cases"]:
        if r["system"] != "murray" or r["case_id"] not in before:
            continue
        was, now = before[r["case_id"]]["score"], r["score"]
        if abs(was - now) > 1e-9:
            rows.append(f"| {r['case_id']} | {was} | {now} | {'improved' if now > was else 'regressed'} | {'yes' if r['split'] == 'holdout' else ''} |")
    if not rows:
        return "No case changed score between the rounds."
    return "| Case | Before | After | | Hold-out |\n|---|---:|---:|---|---|\n" + "\n".join(sorted(rows))


def failures(path: str) -> str:
    """The engineering log as Markdown: what was seen, why, what changed, and what is still open."""
    entries = json.load(open(path))
    out = []
    for status, heading in (("fixed", "### Failures we found, and what we changed"), ("open", "### Where Murray still fails")):
        rows = [e for e in entries if e.get("status", "fixed") == status]
        if not rows:
            continue
        out.append(heading + "\n")
        for e in rows:
            where = ", ".join(x for x in (e.get("round"), e.get("cases")) if x)
            out.append(f"**{e['title']}**" + (f" ({where})" if where else "") + "\n")
            out.append(f"- Seen: {e['observed']}")
            out.append(f"- Why: {e['root_cause']}")
            out.append(f"- {'Changed' if status == 'fixed' else 'What we would try next'}: {e['fix']}\n")
    return "\n".join(out)


def main(paths: list[str]) -> int:
    if paths and paths[0] == "--failures":
        print(failures(paths[1]))
        return 0
    rounds = [json.load(open(p)) for p in paths]
    for i, r in enumerate(rounds):
        note = "" if len(rounds) == 1 else (", as first built" if i == 0 else ", after the fixes" if i == len(rounds) - 1 else "")
        name = f"Round {r['label'][1:]}" if r["label"][:1] == "r" and r["label"][1:].isdigit() else r["label"]
        print(f"### {name}{note}\n")
        print(f"Image `{r['image_tag']}`, prompts `{r['prompt_hash']}`, test set `{r['test_set_sha256']}`, detector `{r['detector_sha256']}`.\n")
        print(headline(r["scorecard"]) + "\n")
        print(table(r["scorecard"]) + "\n")
    if len(rounds) > 1:
        print("### Every case where Murray's score moved between the rounds\n")
        print(flips(rounds[0], rounds[-1]) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
