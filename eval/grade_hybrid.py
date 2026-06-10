#!/usr/bin/env python3
"""Hybrid grader. Deterministic categories (exact numbers / hashes / plot
presence + tool) are scored by run_eval.score_one. Semantic categories are
scored by Claude-as-judge verdicts cached in judge_verdicts.jsonl (keyed by
case_id + sha1(answer); fallback case_id + model_short). Empty answers auto-fail.
No model-under-test calls. Recomputes per-run passed/pass_rate/by_category/cost.

Run: python3 grade_hybrid.py
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

# load judge verdicts: by (case_id, sha1) and (case_id, model_short)
V_HASH, V_MODEL = {}, {}
vpath = EVAL / "judge_verdicts.jsonl"
if vpath.exists():
    for line in vpath.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        v = json.loads(line)
        V_HASH[(v["case_id"], v.get("answer_sha1"))] = v
        V_MODEL[(v["case_id"], v.get("model_short"))] = v

ASK = getattr(RE, "ASK_TIMEOUT_S", 180)


EMPTY_SHA1 = hashlib.sha1(b"").hexdigest()  # da39a3ee… — sha1 of the empty string


def verdict_for(case_id, model_short, answer):
    a = (answer or "").strip()
    if not a:
        return False, "пустой ответ (таймаут/обрыв)", "auto"
    h = hashlib.sha1(a.encode("utf-8")).hexdigest()
    v = V_HASH.get((case_id, h))
    if v is None:  # exact-answer miss → cautious model-level fallback
        cand = V_MODEL.get((case_id, model_short))
        # a stale verdict cached for the EMPTY answer (timeout) must NOT mask a
        # recovered (gap-filled) non-empty answer — force a re-judge instead.
        if cand is not None and cand.get("answer_sha1") != EMPTY_SHA1:
            v = cand
    if v is None:
        return None, "НЕТ ВЕРДИКТА СУДЬИ", "missing"
    return bool(v["pass"]), v.get("reason", ""), v.get("judge", "claude")


def main():
    total_missing = 0
    for bj in sorted((EVAL / "bench").glob("*/bench.json")):
        b = json.loads(bj.read_text(encoding="utf-8"))
        model_short = b["model"].split("/")[-1]
        # Render-quality gate: validate every answer's formulas through real
        # KaTeX once per model. A broken formula (red .katex-error for the reader)
        # fails the case regardless of substring/judge — a defective render must
        # never score high (incident 2026-06-10).
        bk = render_gate.broken_batch([(c["id"], c.get("answer", "") or "") for c in b["cases"]])
        by_cat, passed = {}, 0
        for c in b["cases"]:
            case = cases.get(c["id"])
            cat = c.get("category", "?")
            broken = int((bk.get(c["id"]) or {}).get("broken", 0))
            c["broken_formulas"] = broken
            if case and cat in SEMANTIC:
                jp, reason, judge = verdict_for(c["id"], model_short, c.get("answer", ""))
                if jp is None:
                    total_missing += 1
                c["judge_pass"] = jp
                c["judge_reason"] = reason
                c["judge"] = judge
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
            d = by_cat.setdefault(cat, {"passed": 0, "total": 0})
            d["total"] += 1
            if c.get("pass"):
                d["passed"] += 1; passed += 1
        n = len(b["cases"]) or 1
        b["passed"] = passed
        b["pass_rate"] = round(passed / n, 4)
        b["n"] = len(b["cases"])
        b["by_category"] = by_cat
        tc = round(sum(c.get("cost", 0) or 0 for c in b["cases"]), 6)
        b["total_cost_usd"] = tc
        b["cost_per_q_usd"] = round(tc / n, 6)
        bj.write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{bj.parent.name:36} {passed}/{n} ({b['pass_rate']*100:.1f}%)")
    if total_missing:
        print(f"\n⚠ {total_missing} семантических ответов БЕЗ вердикта судьи — нужно досудить!")


if __name__ == "__main__":
    main()
