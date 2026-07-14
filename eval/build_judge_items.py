#!/usr/bin/env python3
"""Вшить список pending-ответов в wf_meticulous_grade.js (между маркерами).
Workflow args ненадёжен для массивов — список живёт в самом скрипте.
Использование: python3 build_judge_items.py [--limit N] [--filter substr]"""
import json, re, subprocess, sys
from pathlib import Path
EVAL = Path(__file__).resolve().parent
cmd = ["python3", str(EVAL / "pending_judgements.py"), "--json"]
if "--bench" in sys.argv:   # пробросить явную версию (иначе — последняя bench_v*)
    cmd += ["--bench", sys.argv[sys.argv.index("--bench") + 1]]
items = json.loads(subprocess.run(cmd, capture_output=True, text=True).stdout)
def slug(m): return re.sub(r"[^a-z0-9]+", "_", m.lower()).strip("_")
out = [{"model_dir": slug(i["model"]), "model_short": i["model_short"],
        "case_id": i["case_id"], "answer_sha1": i["answer_sha1"]} for i in items]
if "--filter" in sys.argv:
    sub = sys.argv[sys.argv.index("--filter") + 1]
    out = [i for i in out if sub in i["model_dir"] or sub in i["case_id"]]
if "--limit" in sys.argv:
    out = out[: int(sys.argv[sys.argv.index("--limit") + 1])]
wf = EVAL / "wf_meticulous_grade.js"
s = wf.read_text(encoding="utf-8")
s = re.sub(r"// __ITEMS_START__\n.*?\n// __ITEMS_END__",
           "// __ITEMS_START__\nconst ITEMS = " + json.dumps(out, ensure_ascii=False) + "\n// __ITEMS_END__",
           s, flags=re.S)
wf.write_text(s, encoding="utf-8")
print(f"вшито {len(out)} items в {wf.name}")
