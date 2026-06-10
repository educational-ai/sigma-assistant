#!/usr/bin/env python3
"""Concise TG-ready benchmark report from eval/bench/*/bench.json.

Reuses gen_benchmark_page.py's helpers (summarize, short_model, load) so the
numbers always match the published page. Prints a compact Markdown summary:
leaderboard top, best value-for-money (cheapest model on the Pareto frontier),
dead models, and the global cost. Invoke after a full gapfill+regen.

Usage: python3 report_benchmark.py            # print to stdout
"""
import math
from gen_benchmark_page import load, summarize, short_model

def main():
    benches = load()
    if not benches:
        print("нет данных bench.json"); return
    summ = {b["model"]: summarize(b) for b in benches}
    live = [b for b in benches if not summ[b["model"]]["dead"]]
    dead = [b for b in benches if summ[b["model"]]["dead"]]
    order = sorted(live, key=lambda b: -summ[b["model"]]["rate"])
    if not order:
        order, dead = benches, []

    n_q = order[0]["n"] if order else 0
    best = order[0]
    bs = summ[best["model"]]

    # Pareto frontier on (cost, rate); pick cheapest frontier model with rate>=0.8
    pts = [(b.get("total_cost_usd") or 0, summ[b["model"]]["rate"], short_model(b["model"]))
           for b in live if (b.get("total_cost_usd") or 0) > 0]
    front = sorted((p for p in pts if not any(q[0] < p[0] and q[1] > p[1] for q in pts)),
                   key=lambda p: p[0])
    value = next((p for p in front if p[1] >= 0.8), None)

    lines = []
    lines.append(f"🏁 *Бенчмарк агента Σ* — {len(live)} живых моделей, {n_q} вопросов")
    lines.append("")
    lines.append("*Топ-5 (чисто пройдено):*")
    for i, b in enumerate(order[:5], 1):
        s = summ[b["model"]]
        cost = b.get("total_cost_usd")
        cstr = f"${cost:.3f}" if cost else "—"
        medal = ["🥇","🥈","🥉","4.","5."][i-1]
        lines.append(f"{medal} {short_model(b['model'])} — {s['clean']}/{s['n']} ({s['rate']*100:.0f}%), {cstr}")
    lines.append("")
    if value:
        lines.append(f"💰 *Лучшая цена/качество:* {value[2]} — {value[1]*100:.0f}% за ${value[0]:.3f}")
    total = sum((b.get('total_cost_usd') or 0) for b in benches)
    lines.append(f"💵 *Стоимость всего прогона:* ${total:.2f} (фактические списания OpenRouter)")

    # gap-filled semantic answers still scored by rubric, not the Claude judge —
    # surface the targeted re-judge work-list so it isn't silently skipped.
    try:
        import subprocess, json as _json
        from pathlib import Path as _P
        out = subprocess.run(["python3", str(_P(__file__).parent/"eval"/"pending_judgements.py"), "--json"],
                             capture_output=True, text=True, timeout=30)
        pend = _json.loads(out.stdout or "[]")
        if pend:
            lines.append("")
            lines.append(f"⚠️ *{len(pend)} семантич. ответов ждут adversarial-judge* "
                         f"(сейчас по рубрике). Pipeline: pending_judgements.py --json → "
                         f"wf_adversarial_grade.js → persist_verdicts.py → grade_hybrid.py → regen")
    except Exception:
        pass
    if dead:
        lines.append("")
        lines.append(f"⚰️ *Мёртвые (инфра-фейл, вне лидерборда):* " +
                     ", ".join(short_model(b['model']) for b in dead))
    lines.append("")
    lines.append("🔗 sigma.fmin.xyz/benchmark")
    print("\n".join(lines))

if __name__ == "__main__":
    main()
