#!/usr/bin/env python3
"""Rubric-aware grader. Like grade_hybrid.py, but SEMANTIC cases get a continuous
0..1 rubric_score (per-criterion levels from rubric_verdicts.jsonl + AUTO criteria
computed from run data) instead of a binary judge pass. Deterministic categories
(compute/plot) still scored by run_eval.score_one. Render gates still cap to 0.

Writes per case: rubric_score, rubric_capped, rubric_detail, and a derived `pass`
(rubric_score ≥ rubric_score.PASS_THRESHOLD). Recomputes per-run avg_rubric_score.

Run: python3 grade_rubric.py
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
import rubric_score as RS

SEMANTIC = {"rag_basic", "definition", "structural", "out_of_scope", "multi_hop", "vision_refine"}

cases = {}
for line in (EVAL / "cases.jsonl").read_text(encoding="utf-8").splitlines():
    if line.strip():
        c = json.loads(line); cases[c["id"]] = c

# rubric verdicts: by (case_id, sha1) and (case_id, model_short)
RV_HASH, RV_MODEL = {}, {}
rvp = EVAL / "rubric_verdicts.jsonl"
if rvp.exists():
    for line in rvp.read_text(encoding="utf-8").splitlines():
        if line.strip():
            v = json.loads(line)
            RV_HASH[(v["case_id"], v.get("answer_sha1"))] = v
            RV_MODEL[(v["case_id"], v.get("model_short"))] = v

ASK = getattr(RE, "ASK_TIMEOUT_S", 180)
EMPTY_SHA1 = hashlib.sha1(b"").hexdigest()


def levels_for(case_id, model_short, answer):
    a = (answer or "").strip()
    if not a:
        return None  # empty → handled as hard fail upstream
    h = hashlib.sha1(a.encode("utf-8")).hexdigest()
    v = RV_HASH.get((case_id, h))
    if v is None:
        cand = RV_MODEL.get((case_id, model_short))
        if cand is not None and cand.get("answer_sha1") != EMPTY_SHA1:
            v = cand
    if v is None:
        return None
    return {cr["id"]: cr["level"] for cr in v.get("criteria", [])}


def main():
    total_missing = 0
    for bj in sorted((EVAL / "bench").glob("*/bench.json")):
        b = json.loads(bj.read_text(encoding="utf-8"))
        model_short = b["model"].split("/")[-1]
        bk = render_gate.broken_batch([(c["id"], c.get("answer", "") or "") for c in b["cases"]])
        scores = []
        for c in b["cases"]:
            case = cases.get(c["id"])
            cat = c.get("category", "?")
            gd = bk.get(c["id"]) or {}
            broken = int(gd.get("broken", 0))
            raw_def = render_gate.raw_render_defective(
                int(gd.get("dollar", 0)), int(gd.get("raw_unrendered", 0)))
            c["broken_formulas"] = broken
            c["raw_unrendered"] = int(gd.get("raw_unrendered", 0))

            if case and cat in SEMANTIC:
                ans = c.get("answer", "") or ""
                levels = levels_for(c["id"], model_short, ans)
                # obs for AUTO criteria (tools / image)
                tools = c.get("tools") or []
                obs = {
                    "trace": [{"tool": t} for t in tools],
                    "answer": ans, "images": c.get("images", 0) or 0,
                }
                tool_match = RE.score_one(case, obs)["tool_match"]
                n_python = sum(1 for t in tools if "python" in str(t).lower())
                rs_obs = {"tool_match": tool_match, "n_tools": len(tools),
                          "images": c.get("images", 0) or 0,
                          "answer_len": len(ans.strip()), "n_python": n_python}
                if not ans.strip():
                    c["rubric_score"] = 0.0
                    c["rubric_capped"] = True
                    c["rubric_detail"] = [{"id": "_empty", "level": "none"}]
                    c["pass"] = False
                elif levels is None:
                    total_missing += 1
                    c["rubric_score"] = None
                    c["rubric_detail"] = "НЕТ РУБРИЧНОГО ВЕРДИКТА"
                    c["pass"] = False
                else:
                    r = RS.score_answer(c["id"], levels, rs_obs)
                    c["rubric_score"] = r["score"]
                    c["rubric_capped"] = r["capped"]
                    c["rubric_detail"] = r["per_criterion"]
                    c["pass"] = r["passed"]
                    c["answer_match"] = bool(r["passed"])
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
                # deterministic cases score 0/1 in the rubric average
                c["rubric_score"] = 1.0 if sc["pass"] else 0.0

            # Render gates over BOTH paths: defective render → score 0 + fail.
            if (broken > 0 or raw_def) and c.get("rubric_score"):
                why = (f"{broken} битых формул" if broken > 0
                       else f"{c['raw_unrendered']} формул в \\(…\\)/\\[…\\] (сайт рендерит только $/$$)")
                c["rubric_score"] = 0.0
                c["rubric_capped"] = True
                c["pass"] = False
                c["answer_match"] = False
                c["judge_reason"] = f"⚠ {why} — читатель видит сырой/битый LaTeX"

            if c.get("rubric_score") is not None:
                scores.append(c["rubric_score"])

        n = len(b["cases"]) or 1
        b["avg_rubric_score"] = round(sum(scores) / n, 4) if scores else 0.0
        b["passed"] = sum(1 for c in b["cases"] if c.get("pass"))
        b["pass_rate"] = round(b["passed"] / n, 4)
        b["n"] = len(b["cases"])
        tc = round(sum(c.get("cost", 0) or 0 for c in b["cases"]), 6)
        b["total_cost_usd"] = tc
        b["cost_per_q_usd"] = round(tc / n, 6)
        bj.write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{bj.parent.name:34} rubric {b['avg_rubric_score']*100:5.1f}%  "
              f"(pass≥{int(RS.PASS_THRESHOLD*100)}%: {b['passed']}/{n})")
    if total_missing:
        print(f"\n⚠ {total_missing} семантических ответов БЕЗ рубричного вердикта — досудить!")


if __name__ == "__main__":
    main()
