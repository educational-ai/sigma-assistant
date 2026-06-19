#!/usr/bin/env python3
"""Persist rubric-workflow verdicts into rubric_verdicts.jsonl.

wf_rubric_grade.js returns finalVerdicts = [{case_id, verdicts:[{model_short,
criteria:[{id, level, note}]}]}] but does NOT compute answer_sha1 nor write the
cache. grade_hybrid.py looks rubric verdicts up by (case_id, sha1(answer)). This
bridges that: for each verdict it finds the model's CURRENT answer in bench.json,
computes the sha1, and appends an enriched record. Idempotent.

Usage:
  python3 persist_rubric_verdicts.py < finalVerdicts.json
  python3 persist_rubric_verdicts.py finalVerdicts.json
"""
import json, hashlib, sys
from pathlib import Path

EVAL = Path(__file__).resolve().parent
VP = EVAL / "rubric_verdicts.jsonl"
JUDGE_TAG = "rubric-workflow-final"


def load_input():
    raw = (Path(sys.argv[1]).read_text(encoding="utf-8") if len(sys.argv) > 1
           else sys.stdin.read())
    data = json.loads(raw)
    return data.get("finalVerdicts", data) if isinstance(data, dict) else data


ANS = {}
for bj in (EVAL / "bench").glob("*/bench.json"):
    b = json.loads(bj.read_text(encoding="utf-8"))
    ms = b["model"].split("/")[-1]
    for c in b["cases"]:
        ANS[(c["id"], ms)] = (c.get("answer") or "").strip()

seen = set()
if VP.exists():
    for line in VP.read_text(encoding="utf-8").splitlines():
        if line.strip():
            v = json.loads(line)
            seen.add((v["case_id"], v.get("answer_sha1"), v.get("model_short")))

groups = load_input()
added, skipped, missing = 0, 0, 0
out_lines = []
for g in groups:
    cid = g["case_id"]
    for v in g.get("verdicts", []):
        ms = v["model_short"]
        ans = ANS.get((cid, ms))
        if ans is None:
            missing += 1
            continue
        sha1 = hashlib.sha1(ans.encode("utf-8")).hexdigest()
        key = (cid, sha1, ms)
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        out_lines.append(json.dumps({
            "case_id": cid, "model_short": ms, "answer_sha1": sha1,
            "criteria": v.get("criteria", []), "judge": JUDGE_TAG,
        }, ensure_ascii=False))
        added += 1

if out_lines:
    with VP.open("a", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")
print(f"persisted {added} rubric verdicts (skipped {skipped} dup, {missing} model-not-in-bench) → {VP.name}")
