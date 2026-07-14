#!/usr/bin/env python3
"""Hybrid grader. Deterministic categories (exact numbers / hashes / plot
presence + tool) are scored by run_eval.score_one. Semantic categories are
scored by Claude-as-judge verdicts cached in judge_verdicts.jsonl, keyed by
case_id + sha1(answer) — EXACT match only. A verdict for a different answer
(same model, earlier run) is a verdict about a different text; inheriting it
was the 2026-07-13 audit critical #1 (232/304 оценок про стёртые ответы).
Miss → the case is PENDING: excluded from BOTH numerator and denominator
(never published as fail — audit critical #2), and listed for the judge.
Empty answers auto-fail. No model-under-test calls. Recomputes per-run
passed/pass_rate/by_category/cost.

Run: python3 grade_hybrid.py [--strict]   # --strict: exit 2 если есть pending
"""
import json, hashlib, sys, types
from pathlib import Path

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL)); sys.path.insert(0, str(EVAL.parent))
if "patchright" not in sys.modules:                      # grader-only import
    m = types.ModuleType("patchright"); a = types.ModuleType("patchright.async_api")
    a.async_playwright = lambda *x, **k: None; m.async_api = a
    sys.modules["patchright"] = m; sys.modules["patchright.async_api"] = a
import run_eval as RE
import render_gate

# vision_refine moved to judge: the "0.5" substring was illegitimate (any η<1
# converges); a judge grades the diagnosis + a working step + convergence.
SEMANTIC = {"rag_basic", "definition", "structural", "out_of_scope", "multi_hop", "vision_refine"}

cases = {}
for line in (EVAL / "cases.jsonl").read_text(encoding="utf-8").splitlines():
    if line.strip():
        c = json.loads(line); cases[c["id"]] = c

# load judge verdicts: keyed by (case_id, sha1(answer)) — exact answer only
V_HASH = {}
vpath = EVAL / "judge_verdicts.jsonl"
if vpath.exists():
    for line in vpath.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        v = json.loads(line)
        V_HASH[(v["case_id"], v.get("answer_sha1"))] = v

ASK = getattr(RE, "ASK_TIMEOUT_S", 180)


def verdict_for(case_id, model_short, answer):
    a = (answer or "").strip()
    if not a:
        return False, "пустой ответ (таймаут/обрыв)", "auto"
    h = hashlib.sha1(a.encode("utf-8")).hexdigest()
    v = V_HASH.get((case_id, h))
    if v is None:
        return None, "ждёт судью", "missing"
    return bool(v["pass"]), v.get("reason", ""), v.get("judge", "claude")


def main():
    total_missing = 0
    for bj in sorted(sorted(EVAL.glob("bench_v*"), key=lambda p: (len(p.name), p.name))[-1].glob("*/bench.json")):
        b = json.loads(bj.read_text(encoding="utf-8"))
        model_short = b["model"].split("/")[-1]
        # Render-quality gate: validate every answer's formulas through real
        # KaTeX once per model. A broken formula (red .katex-error for the reader)
        # fails the case regardless of substring/judge — a defective render must
        # never score high (incident 2026-06-10).
        bk = render_gate.broken_batch([(c["id"], c.get("answer", "") or "") for c in b["cases"]])
        by_cat, passed, pending = {}, 0, 0
        for c in b["cases"]:
            case = cases.get(c["id"])
            cat = c.get("category", "?")
            gd = bk.get(c["id"]) or {}
            broken = int(gd.get("broken", 0))
            c["broken_formulas"] = broken
            c["raw_unrendered"] = int(gd.get("raw_unrendered", 0))
            if case and cat in SEMANTIC:
                jp, reason, judge = verdict_for(c["id"], model_short, c.get("answer", ""))
                c["judge_pass"] = jp
                c["judge_reason"] = reason
                c["judge"] = judge
                if jp is None:
                    # Not judged yet: NOT a fail, NOT a pass — pending. Excluded
                    # from numerator AND denominator so the leaderboard never
                    # publishes an unjudged answer as ✕ (audit critical #2).
                    total_missing += 1
                    pending += 1
                    c["judge_pending"] = True
                    c["pass"] = None
                    c["answer_match"] = None
                    continue
                c.pop("judge_pending", None)
                # tool dimension reported but does NOT gate semantic pass
                c["answer_match"] = bool(jp)
                c["pass"] = bool(jp)
            elif case:
                obs = {
                    "trace": [{"tool": t} for t in (c.get("tools") or [])],
                    "answer": c.get("answer", "") or "",
                    "images": c.get("images", 0) or 0,
                    "timed_out": (c.get("elapsed", 0) or 0) >= ASK,
                    "broken_formulas": broken,
                }
                sc = RE.score_one(case, obs)
                for k in ("tool_match", "answer_match", "visual_match", "missing", "pass"):
                    c[k] = sc[k]
            # Broken-render gate over BOTH paths: defective formulas → fail.
            if broken > 0 and c.get("pass"):
                c["pass"] = False
                c["answer_match"] = False
                note = f"⚠ {broken} битых формул (KaTeX не рендерит)"
                c["judge_reason"] = (c.get("judge_reason") or "").strip()
                c["judge_reason"] = (c["judge_reason"] + "; " + note).lstrip("; ") if c["judge_reason"] else note
            # NB: no raw-delimiter gate here — the dev renderer handles \(…\)/\[…\]
            # via KaTeX just fine (audit major #10: the gate flipped 11 valid cases).
            d = by_cat.setdefault(cat, {"passed": 0, "total": 0})
            d["total"] += 1
            if c.get("pass"):
                d["passed"] += 1; passed += 1
        n = len(b["cases"])
        judged = max(n - pending, 1)
        b["passed"] = passed
        b["pass_rate"] = round(passed / judged, 4)
        b["n"] = n
        b["judge_pending"] = pending
        b["by_category"] = by_cat
        tc = round(sum(c.get("cost", 0) or 0 for c in b["cases"]), 6)
        b["total_cost_usd"] = tc
        b["cost_per_q_usd"] = round(tc / (n or 1), 6)
        bj.write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")
        pend_note = f"  ⏳ {pending} ждёт судью" if pending else ""
        print(f"{bj.parent.name:36} {passed}/{n - pending} ({b['pass_rate']*100:.1f}%){pend_note}")
    if total_missing:
        print(f"\n⚠ {total_missing} семантических ответов БЕЗ вердикта судьи — досудить "
              f"(pending_judgements.py → судья → persist_verdicts.py → grade_hybrid.py)")
        if "--strict" in sys.argv:
            sys.exit(2)


if __name__ == "__main__":
    main()
