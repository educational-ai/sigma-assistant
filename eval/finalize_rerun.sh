#!/bin/bash
# Independent watcher: when _rerun_all.sh finishes the 18-model re-run, self-trigger
# a Claude session to run the FINAL grading pipeline. Decoupled from the interactive
# session so the grading happens even if that session ends during the multi-hour run.
set -u
LOG=/tmp/_rerun_all.log
while ! grep -q "RERUN-ALL FINISHED" "$LOG" 2>/dev/null; do sleep 60; done
sleep 10
PROMPT='Перегон 18 моделей Σ-бенча с raw-захватом ЗАВЕРШЁН (см /tmp/_rerun_all.log). Запусти ФИНАЛЬНЫЙ грейдинг (auto, внутри мандата Даниила «прогони остальное + публикуй»): (1) свежие ответы → новые sha1, поэтому семантические кейсы НУЖНО переграйдить — Workflow tool scriptPath /root/sigma_assistant/eval/wf_adversarial_grade.js (судья→рефутер→аудит); (2) finalVerdicts в файл → python3 /root/sigma_assistant/eval/persist_verdicts.py <file>; (3) ПРОЧТИ auditReport, поправь нелегитимные вердикты в judge_verdicts.jsonl; (4) python3 /root/sigma_assistant/eval/grade_hybrid.py (гейт на битый рендер УЖЕ встроен, throwOnError:false как страница); (5) cd /root/sigma_assistant && python3 gen_benchmark_page.py; (6) проверь sigma.fmin.xyz/benchmark в браузере (patchright): топ-кейсы рендерятся, формулы KaTeX, 0 битых/0 сырых S; (7) визуальный судья: python3 eval/render_answer_shots.py --all → выборочно проверь PNG на визуальную адекватность; (8) пришли Даниилу финальный отчёт report_benchmark.py + краткий итог что изменилось vs старый борд. НЕ коммить без отдельного шага git в приватный sigma-assistant (это ок — репо приватный). Остаток OpenRouter проверь auth/key.'
curl -s -X POST localhost:9357 -d "$PROMPT" >/dev/null 2>&1
echo "[$(date +%H:%M)] finalize trigger fired" >> "$LOG"
