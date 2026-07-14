#!/usr/bin/env python3
"""Persist adversarial-workflow verdicts into judge_verdicts.jsonl.

wf_adversarial_grade.js returns finalVerdicts = [{case_id, verdicts:[{model_short,
pass, reason}]}] but does NOT compute answer_sha1 nor write the cache. grade_hybrid.py
looks verdicts up by (case_id, sha1(current answer)). This script bridges that:
for each verdict it finds the model's CURRENT answer in bench.json, computes the
sha1, and appends an enriched record. Idempotent — skips a record already present
for the same (case_id, answer_sha1, model_short).

Usage:
  python3 persist_verdicts.py < finalVerdicts.json     # array of {case_id, verdicts}
  python3 persist_verdicts.py finalVerdicts.json
"""
import json, hashlib, sys, time
from pathlib import Path

EVAL = Path(__file__).resolve().parent
VP = EVAL / "judge_verdicts.jsonl"
JUDGE_TAG = "adversarial-workflow-final"

def load_input():
    raw = (Path(sys.argv[1]).read_text(encoding="utf-8") if len(sys.argv) > 1
           else sys.stdin.read())
    data = json.loads(raw)
    # accept either {finalVerdicts:[...]} or a bare [...]
    return data.get("finalVerdicts", data) if isinstance(data, dict) else data

# current answer per (case_id, model_short) from bench.json
# --bench bench_v1 = явная версия; без флага — последняя bench_v*
if "--bench" in sys.argv:
    i = sys.argv.index("--bench")
    BDIR = EVAL / sys.argv[i + 1]
    del sys.argv[i:i + 2]   # не мешать позиционному аргументу-файлу
else:
    BDIR = sorted(EVAL.glob("bench_v*"), key=lambda p: (len(p.name), p.name))[-1]
ANS = {}
for bj in BDIR.glob("*/bench.json"):
    b = json.loads(bj.read_text(encoding="utf-8"))
    ms = b["model"].split("/")[-1]
    for c in b["cases"]:
        ANS[(c["id"], ms)] = (c.get("answer") or "").strip()

# existing keys for idempotency
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
        # ts + answer snippet = воспроизводимый аудит-трейл: даже если ответ
        # позже стёрт из bench.json, видно, ЧТО читал судья (audit major #16).
        out_lines.append(json.dumps({
            "case_id": cid, "model_short": ms, "answer_sha1": sha1,
            "pass": bool(v["pass"]), "reason": v.get("reason", ""),
            "judge": JUDGE_TAG,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "answer_snippet": ans[:200],
        }, ensure_ascii=False))
        added += 1

if out_lines:
    with VP.open("a", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")
print(f"persisted {added} verdicts (skipped {skipped} dup, {missing} model-not-in-bench) → {VP.name}")
