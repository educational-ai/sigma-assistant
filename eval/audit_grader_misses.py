#!/usr/bin/env python3
"""audit_grader_misses.py — найти кейсы, которые грейдер ЗАЧЁЛ (pass=True),
хотя по объективным признакам ответ неполный: не вызвал python там, где он
нужен; не приложил картинку, когда просили показать график; не искал по
учебнику, когда вопрос про учебник; выдал пустой/обрубок.

Это PRE-FILTER (подсказка, что смотреть глазами), а НЕ финальный вердикт:
часть флагов — ложные (например, короткий ответ на приветствие = норм).

Запуск:  python3 eval/audit_grader_misses.py
"""
import glob
import json
import os
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
exp = {}
for f in ["eval/cases.jsonl", "eval/cases_extra.jsonl"]:
    p = os.path.join(ROOT, f)
    if os.path.exists(p):
        for line in open(p):
            c = json.loads(line)
            exp[c["id"]] = c

susp = []
for d in sorted(glob.glob(os.path.join(ROOT, "eval/bench_v*/*/bench.json"))):
    model = os.path.basename(os.path.dirname(d))
    j = json.load(open(d))
    for c in j["cases"]:
        if not c.get("pass"):
            continue  # смотрим только то, что грейдер ЗАЧЁЛ
        e = exp.get(c["id"], {})
        et = str(e.get("expected_tools", ""))
        ev = str(e.get("expected_visual", ""))
        tools = c.get("tools") or []
        imgs = c.get("images", 0) or 0
        ans = (c.get("answer") or "").strip()
        why = []
        if "python" in et and "python" not in tools:
            why.append("python не вызван")
        if ev == "True" and imgs == 0:
            why.append("картинка не приложена")
        if ("search_textbook" in et or "read_chapter" in et) and not any(
            t in tools for t in ("search_textbook", "read_chapter")
        ):
            why.append("поиск по учебнику не сделан")
        if c.get("no_answer") or len(ans) < 25:
            why.append("пустой/обрубок")
        if why:
            susp.append({
                "model": model, "id": c["id"], "category": c.get("category"),
                "tools": tools, "images": imgs,
                "rubric_score": c.get("rubric_score"),
                "flags": why,
            })

print(f"подозрительных зачтённых (pass=True + флаг): {len(susp)}")
print("по типу:", dict(Counter(w for s in susp for w in s["flags"])))
print()
for s in susp:
    print(f"{s['model'][:22]:22} | {s['id'][:28]:28} | {s['category']:13} "
          f"| tools={'+'.join(s['tools']) or '∅':32} img={s['images']} "
          f"| {'; '.join(s['flags'])}")

# машиночитаемый вывод для разметки
out = os.path.join(ROOT, "eval/audit_grader_misses.json")
json.dump(susp, open(out, "w"), ensure_ascii=False, indent=2)
print(f"\n→ {out}")
