#!/usr/bin/env python3
"""List SEMANTIC cases whose CURRENT answer has no Claude-judge verdict.

After gapfill_empties.py fills timed-out empties with fresh answers, those answers
carry a new sha1 that the cached judge_verdicts.jsonl (keyed by case_id+sha1) does
not cover — so grade_hybrid.py would mark them "НЕТ ВЕРДИКТА"→fail. This script
produces the exact work-list (model, case_id, category, answer) for a TARGETED
adversarial judge pass (wf_adversarial_grade.js) — judge only the new answers, not
all 304 again, then append to judge_verdicts.jsonl and run grade_hybrid.py.

Usage:
  python3 pending_judgements.py            # human summary
  python3 pending_judgements.py --json     # JSON array for the judge workflow
"""
import json, hashlib, sys
from pathlib import Path

EVAL = Path(__file__).resolve().parent
SEMANTIC = {"rag_basic", "definition", "structural", "out_of_scope", "multi_hop", "vision_refine"}


def bench_dir():
    """--bench bench_v1 = явная версия; без флага — последняя bench_v*."""
    if "--bench" in sys.argv:
        return EVAL / sys.argv[sys.argv.index("--bench") + 1]
    return sorted(EVAL.glob("bench_v*"), key=lambda p: (len(p.name), p.name))[-1]

# Verdict keys: (case_id, sha1(answer)) — EXACT answer only, mirroring
# grade_hybrid.verdict_for. A verdict cached for the same model but a different
# answer is a verdict about a different text (audit 2026-07-13 critical #1).
V_HASH = set()
vp = EVAL / "judge_verdicts.jsonl"
if vp.exists():
    for line in vp.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        v = json.loads(line)
        V_HASH.add((v["case_id"], v.get("answer_sha1")))

# case categories
cats = {}
for line in (EVAL / "cases.jsonl").read_text(encoding="utf-8").splitlines():
    if line.strip():
        c = json.loads(line); cats[c["id"]] = c.get("category", "?")

pending = []
for bj in sorted(bench_dir().glob("*/bench.json")):
    b = json.loads(bj.read_text(encoding="utf-8"))
    model_short = b["model"].split("/")[-1]
    for c in b["cases"]:
        cid = c["id"]
        if cats.get(cid) not in SEMANTIC:
            continue
        ans = (c.get("answer") or "").strip()
        if not ans:
            continue  # empty → auto-fail, no judge needed
        h = hashlib.sha1(ans.encode("utf-8")).hexdigest()
        if (cid, h) in V_HASH:
            continue  # this exact answer already judged
        pending.append({
            "model": b["model"], "model_short": model_short,
            "case_id": cid, "category": cats.get(cid),
            "answer_sha1": h, "answer": ans,
        })

if "--json" in sys.argv:
    print(json.dumps(pending, ensure_ascii=False))
else:
    print(f"{len(pending)} SEMANTIC answers без вердикта судьи "
          f"(нужен таргетный adversarial judge pass):")
    by_model = {}
    for p in pending:
        by_model.setdefault(p["model_short"], []).append(p["case_id"])
    for m, ids in sorted(by_model.items()):
        print(f"  {m:38} {len(ids):2}  {', '.join(ids)}")
