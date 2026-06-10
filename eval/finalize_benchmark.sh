#!/usr/bin/env bash
# Incremental publisher + finalize watcher (incident 2026-06-10 long-run).
# gapfill_empties.py runs serially over ~9 models and can take many hours, merging
# per-model into eval/bench/*/bench.json as each model completes. Publishing only at
# the very end leaves the page stale for hours and hostage to full completion.
# This watcher therefore REGENERATES the static page incrementally — every time any
# bench.json changes — so each model's recovery shows within minutes. When gapfill
# exits it does a final regen + headless render-check + DIRECT report.
# gen_benchmark_page.load() try/excepts per file, so a mid-write partial JSON is just
# skipped that round and picked up next round (race-safe). No git commit (needs OK).
set -uo pipefail
ROOT=/root/sigma_assistant
LOG=$ROOT/eval/_finalize.log
BENCH=$ROOT/eval/bench

echo "=== finalize watcher start $(date '+%F %T') ===" >> "$LOG"

regen() {  # regenerate page; returns nonzero on failure
  ( cd "$ROOT" && python3 gen_benchmark_page.py ) >> "$LOG" 2>&1
}

# fingerprint of all bench.json mtimes — changes when any model merges
fp() { stat -c '%Y' "$BENCH"/*/bench.json 2>/dev/null | sort | md5sum | cut -d' ' -f1; }

last=""
# 1. Incremental loop while gapfill runs: republish whenever data changes
while pgrep -f gapfill_empties.py >/dev/null 2>&1; do
  cur=$(fp)
  if [ "$cur" != "$last" ]; then
    if regen; then
      echo "incremental regen $(date '+%F %T')" >> "$LOG"
      last=$cur
    else
      echo "incremental GEN FAILED (likely mid-write) $(date '+%F %T'), retry next loop" >> "$LOG"
    fi
  fi
  sleep 90
done
echo "gapfill exited $(date '+%F %T')" >> "$LOG"
sleep 10  # let final bench.json write + .env restore settle

# 1b. Replace the 2 rate-limited :free models (gemma-4, kimi — scored 0/29 & 1/29 purely
# from free-tier empties) with their PAID endpoints, so the final board has real scores.
# Serial after gapfill (both swap SIGMA_MODEL on the live server). Paid-only, no scope
# creep into the 6 candidate models. Guard prevents a re-run on watcher restart.
if [ ! -f "$ROOT/eval/.paid_replace_done" ] && ! pgrep -f collect_new_cheap.py >/dev/null 2>&1; then
  echo "collect paid gemma-4 + kimi $(date '+%F %T')" >> "$LOG"
  ( cd "$ROOT/eval" && python3 collect_new_cheap.py google/gemma-4-26b-a4b-it moonshotai/kimi-k2.6 ) >> "$LOG" 2>&1 \
    && touch "$ROOT/eval/.paid_replace_done" \
    || echo "paid-replace collect failed (non-fatal, board still publishes)" >> "$LOG"
  # drop the dead :free row only once its PAID replacement actually landed (avoid dupes)
  [ -f "$BENCH/google_gemma_4_26b_a4b_it/bench.json" ] && rm -rf "$BENCH/google_gemma_4_26b_a4b_it_free"
  [ -f "$BENCH/moonshotai_kimi_k2_6/bench.json" ] && rm -rf "$BENCH/moonshotai_kimi_k2_6_free"
  regen || true   # publish the new rows immediately
fi

# 2. Final regen
regen || { echo "FINAL GEN FAILED" >> "$LOG"; exit 1; }

# 3. Headless sanity check: page loads, leaderboard present, no breakage markers
python3 - <<'PY' >> "$LOG" 2>&1
import asyncio
from patchright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width":1400,"height":1000})
        await pg.goto("https://sigma.fmin.xyz/benchmark", wait_until="networkidle", timeout=60000)
        await pg.wait_for_timeout(1000)
        body = await pg.inner_text("body")
        bad = [m for m in ("undefined","NaN","{{","}}","Traceback") if m in body]
        assert "Лидерборд" in body, "no leaderboard"
        assert not bad, f"breakage markers: {bad}"
        await pg.screenshot(path="/tmp/bench_finalized.png", full_page=False)
        print("RENDER OK len", len(body))
        await b.close()
asyncio.run(main())
PY

# 4. Notify (DIRECT trigger) with the full report (top-5 + value pick + cost + dead).
MSG=$(cd "$ROOT" && python3 report_benchmark.py 2>>"$LOG")
[ -z "$MSG" ] && MSG="🏁 Бенчмарк: gapfill завершён, страница пересобрана. sigma.fmin.xyz/benchmark"
curl -s -d "DIRECT:${MSG}" localhost:9357 >> "$LOG" 2>&1 || echo "DIRECT send failed" >> "$LOG"

# 5. Self-trigger the grading session IF semantic answers still await the judge.
#    The adversarial judge needs the Workflow tool (Claude side) — bash can't run it —
#    so wake an interactive session to run the turnkey pipeline. Fires only when there
#    is real work, so it's a no-op if grading is already complete.
PENDING=$(cd "$ROOT" && python3 eval/pending_judgements.py --json 2>/dev/null | python3 -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
if [ "${PENDING:-0}" -gt 0 ]; then
  echo "self-trigger grading: $PENDING pending $(date '+%F %T')" >> "$LOG"
  PROMPT="Gapfill бенчмарка Σ завершён, осталось ${PENDING} семантич. ответов без вердикта судьи. Запусти ФИНАЛЬНЫЙ грейдинг (внутри long-run мандата, auto): (1) python3 /root/sigma_assistant/eval/pending_judgements.py --json — work-list; (2) Workflow tool со scriptPath /root/sigma_assistant/eval/wf_adversarial_grade.js (Claude-судьи: судья→рефутер→аудит); (3) сохрани finalVerdicts в файл и прогони python3 /root/sigma_assistant/eval/persist_verdicts.py <file>; (4) ПРОЧТИ auditReport (критик легитимности): по каждому отмеченному нелегитимному вердикту реши и поправь — пустые/оборванные ответы=незачёт, галлюцинации с зачётом→незачёт, корректные с придиркой→зачёт; правки внеси прямо в judge_verdicts.jsonl (правильный answer_sha1) перед грейдом; (5) python3 /root/sigma_assistant/eval/grade_hybrid.py; (6) cd /root/sigma_assistant && python3 gen_benchmark_page.py; (7) проверь sigma.fmin.xyz/benchmark в браузере (patchright): лидерборд+матрица+Парето рендерятся, нет undefined/NaN; (8) пришли финальный отчёт report_benchmark.py. Потом safe_restart claude-tg для активации send_telegram_message. НЕ коммить бенч без ОК Даниила."
  curl -s -X POST --data-binary @- http://localhost:9357 <<<"$PROMPT" >> "$LOG" 2>&1 || echo "self-trigger failed" >> "$LOG"
fi
echo "=== finalize done $(date '+%F %T') ===" >> "$LOG"
