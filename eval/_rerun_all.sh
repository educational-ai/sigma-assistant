#!/bin/bash
# Re-run all models EXCEPT qwen3.6-flash (already done with the raw-capture fix),
# one at a time, so the public board refreshes each model in place. Top performers
# first. Each iteration: backup+remove that model's stale bench.json → run it →
# bench_models regenerates the page. Stops early if OpenRouter credit runs low.
set -u
cd /root/sigma_assistant
KEY=$(grep -iE "OPENROUTER_API_KEY" .env | head -1 | cut -d= -f2- | tr -d '"'"'"'"')
LOG=/tmp/_rerun_all.log
: > "$LOG"

MODELS=(
 "openai/gpt-5-nano" "stepfun/step-3.7-flash" "bytedance-seed/seed-1.6-flash"
 "google/gemini-3.1-flash-lite" "moonshotai/kimi-k2.6" "qwen/qwen3.5-9b"
 "mistralai/ministral-8b-2512" "openai/gpt-4o-mini" "amazon/nova-lite-v1"
 "mistralai/mistral-small-3.2-24b-instruct" "xiaomi/mimo-v2.5"
 "qwen/qwen3-vl-8b-instruct" "google/gemini-2.5-flash-lite"
 "meta-llama/llama-4-scout" "google/gemma-4-26b-a4b-it"
 "google/gemini-3.5-flash" "google/gemma-3-27b-it" "google/gemma-3-12b-it"
)

remaining() {
  curl -s https://openrouter.ai/api/v1/auth/key -H "Authorization: Bearer $KEY" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['limit_remaining'])" 2>/dev/null
}

mkdir -p eval/_prebench_backup
i=0; n=${#MODELS[@]}
for m in "${MODELS[@]}"; do
  i=$((i+1))
  rem=$(remaining)
  echo "[$(date +%H:%M)] ($i/$n) credit_remaining=\$$rem · next=$m" >> "$LOG"
  # Guard: keep a $0.50 safety margin (the priciest single model ~ $0.95).
  low=$(python3 -c "print(1 if (float('${rem:-0}')) < 1.10 else 0)" 2>/dev/null)
  if [ "$low" = "1" ]; then
    echo "[$(date +%H:%M)] STOP: credit \$$rem too low to safely run more. Done $((i-1))/$n." >> "$LOG"
    break
  fi
  slug=$(echo "$m" | sed 's#[/.-]#_#g')
  bj="eval/bench/$slug/bench.json"
  if [ -f "$bj" ]; then cp "$bj" "eval/_prebench_backup/${slug}.json"; rm -f "$bj"; fi
  echo "[$(date +%H:%M)] running $m (slug=$slug)…" >> "$LOG"
  python3 bench_models.py "$m" >> "$LOG" 2>&1
  echo "[$(date +%H:%M)] done $m" >> "$LOG"
done
echo "[$(date +%H:%M)] RERUN-ALL FINISHED ($i models attempted)" >> "$LOG"
