#!/usr/bin/env python3
"""Benchmark health check — catch DEAD or STALE grading infrastructure.

Twice this harness shipped a grader that was fully built + unit-tested but never
actually RAN: its verdict cache was empty, so the live board silently fell back
to a cruder path (rubric grader → binary judge) or skipped a dimension entirely
(vision judge → only render_gate). This script makes that failure loud.

For each grading subsystem it checks three things and classifies the result:
  • grader module + its config present?            (built)
  • verdict cache present and non-empty?           (run)
  • do the cached verdicts cover the CURRENT answer of every semantic
    (case, model) pair, keyed by sha1(answer)?     (fresh, not stale)

States:
  WIRED   — built, run, and covers ≥`FRESH_OK`% of current answers
  STALE   — built + run but a chunk of verdicts are keyed to OLD answers
            (the model was re-collected; its verdict no longer matches) ← the
            "18/19 models have stale answers on the board" failure mode
  DEAD    — built but verdict cache empty/missing → grader is decorative
  ABSENT  — subsystem not built (informational, not a failure)

Exit code 1 if any subsystem is DEAD or STALE (so a cron/worker can alert).

Usage: python3 bench_health.py [--json]
"""
import json, hashlib, glob, sys, os
from pathlib import Path

EVAL = Path(__file__).resolve().parent
SEMANTIC = {"rag_basic", "definition", "structural", "out_of_scope", "multi_hop", "vision_refine"}
FRESH_OK = 0.95  # ≥95% of current answers must have a matching verdict to be WIRED


def current_semantic_pairs():
    """{(case_id, model_short): sha1(current answer)} for every semantic pair
    whose current answer is non-empty (empty = auto-fail, no verdict needed)."""
    cases = {}
    for l in (EVAL / "cases.jsonl").read_text(encoding="utf-8").splitlines():
        if l.strip():
            c = json.loads(l); cases[c["id"]] = c
    pairs = {}
    for bj in glob.glob(str(EVAL / "bench" / "*" / "bench.json")):
        b = json.loads(Path(bj).read_text(encoding="utf-8"))
        ms = b["model"].split("/")[-1]
        for c in b["cases"]:
            cs = cases.get(c["id"])
            if cs and cs.get("category") in SEMANTIC:
                ans = (c.get("answer") or "").strip()
                if ans:
                    pairs[(c["id"], ms)] = hashlib.sha1(ans.encode("utf-8")).hexdigest()
    return pairs


def cache_hashes(fname):
    """{(case_id, model_short): set(answer_sha1)} from a verdict jsonl, or None
    if the file is absent."""
    p = EVAL / fname
    if not p.exists():
        return None
    out = {}
    for l in p.read_text(encoding="utf-8").splitlines():
        if l.strip():
            v = json.loads(l)
            out.setdefault((v.get("case_id"), v.get("model_short")), set()).add(v.get("answer_sha1"))
    return out


def classify(built, cache, pairs):
    if not built:
        return "ABSENT", {}
    if cache is None or not cache:
        return "DEAD", {"fresh": 0, "need": len(pairs)}
    fresh = sum(1 for k, h in pairs.items() if h in cache.get(k, set()))
    need = len(pairs) or 1
    stats = {"fresh": fresh, "need": len(pairs), "frac": round(fresh / need, 3)}
    if fresh / need >= FRESH_OK:
        return "WIRED", stats
    if fresh == 0:
        return "DEAD", stats
    return "STALE", stats


SUBSYSTEMS = [
    {"name": "binary-judge", "grader": "grade_hybrid.py", "config": None,
     "cache": "judge_verdicts.jsonl"},
    {"name": "rubric", "grader": "grade_rubric.py", "config": "rubrics.jsonl",
     "cache": "rubric_verdicts.jsonl"},
    {"name": "vision-judge", "grader": "render_answer_shots.py", "config": None,
     "cache": "vision_verdicts.jsonl"},
]


def main():
    pairs = current_semantic_pairs()
    report = []
    bad = False
    for s in SUBSYSTEMS:
        built = (EVAL / s["grader"]).exists() and (s["config"] is None or (EVAL / s["config"]).exists())
        cache = cache_hashes(s["cache"])
        state, stats = classify(built, cache, pairs)
        if state in ("DEAD", "STALE"):
            bad = True
        report.append({"subsystem": s["name"], "state": state, **stats,
                       "cache": s["cache"]})

    if "--json" in sys.argv:
        print(json.dumps({"pairs": len(pairs), "subsystems": report}, ensure_ascii=False, indent=1))
    else:
        icon = {"WIRED": "✅", "STALE": "⚠️ ", "DEAD": "💀", "ABSENT": "·"}
        print(f"Benchmark health — {len(pairs)} семантич. (case,model) пар с непустым ответом\n")
        for r in report:
            cov = (f"{r.get('fresh',0)}/{r.get('need',0)} свежих вердиктов"
                   if r["state"] != "ABSENT" else "не построен")
            print(f"  {icon[r['state']]} {r['subsystem']:14} {r['state']:6} — {cov}  ({r['cache']})")
        if bad:
            print("\n⚠ Есть DEAD/STALE подсистема — грейдер построен, но не прогнан/устарел "
                  "(декоративный код, борд молча падает на грубый путь).")
        else:
            print("\nВсе построенные грейдеры прогнаны и свежи.")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
