#!/usr/bin/env python3
"""Re-score every eval/bench/*/bench.json from STORED answers using the current
grader (run_eval.score_one) + current cases.jsonl. No model calls — pure regrade.
Recomputes per-case pass/answer_match/tool_match/missing and run-level
passed/pass_rate/by_category. Backs up nothing (caller already did)."""
import json, sys, types
from pathlib import Path

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL)); sys.path.insert(0, str(EVAL.parent))
# grader-only import: stub the browser dep run_eval pulls at module top
if "patchright" not in sys.modules:
    m = types.ModuleType("patchright"); a = types.ModuleType("patchright.async_api")
    a.async_playwright = lambda *x, **k: None; m.async_api = a
    sys.modules["patchright"] = m; sys.modules["patchright.async_api"] = a
import run_eval as RE

cases = {}
for line in (EVAL / "cases.jsonl").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line:
        c = json.loads(line); cases[c["id"]] = c

ASK = getattr(RE, "ASK_TIMEOUT_S", 180)
total_flips = 0
for bj in sorted((EVAL / "bench").glob("*/bench.json")):
    b = json.loads(bj.read_text(encoding="utf-8"))
    flips = 0
    by_cat = {}
    passed = 0
    for c in b["cases"]:
        case = cases.get(c["id"])
        if not case:
            # case removed from set — leave record as-is
            pass
        else:
            obs = {
                "trace": [{"tool": t} for t in (c.get("tools") or [])],
                "answer": c.get("answer", "") or "",
                "images": c.get("images", 0) or 0,
                "timed_out": (c.get("elapsed", 0) or 0) >= ASK,
            }
            sc = RE.score_one(case, obs)
            was = c.get("pass")
            for k in ("tool_match", "answer_match", "visual_match", "missing", "pass"):
                c[k] = sc[k]
            if "unexpected" in sc: c["unexpected"] = sc["unexpected"]
            if "garbage" in sc: c["garbage"] = sc["garbage"]
            if was != c["pass"]:
                flips += 1
        cat = c.get("category", "?")
        d = by_cat.setdefault(cat, {"passed": 0, "total": 0})
        d["total"] += 1
        if c.get("pass"): d["passed"] += 1; passed += 1
    n = len(b["cases"]) or 1
    b["passed"] = passed
    b["pass_rate"] = round(passed / n, 4)
    b["by_category"] = by_cat
    bj.write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")
    total_flips += flips
    print(f"{bj.parent.name:34} pass {passed}/{n} ({b['pass_rate']*100:.1f}%)  flips:{flips}")
print(f"\nTotal pass-flips: {total_flips}")
