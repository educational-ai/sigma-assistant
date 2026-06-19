# Sigma benchmark — grading pipeline

How the leaderboard at `sigma.fmin.xyz/benchmark` gets its scores, and the order to
run things in. Written 2026-06-10 during the gapfill+adversarial-grade work.

## Data flow

```
collect (bench_models.py) ──> eval/bench/<model>/bench.json   # answers + rubric scores
        │
        ├─ gapfill_empties.py        # re-run TIMED-OUT empties with adaptive timeout,
        │                            #   MERGE recovered answers back per-model
        │
        ├─ wf_adversarial_grade.js   # Workflow tool: Claude judges (judge→refute→audit)
        │      (returns finalVerdicts, does NOT persist)
        ├─ persist_verdicts.py       # finalVerdicts → judge_verdicts.jsonl (+ answer_sha1)
        ├─ grade_hybrid.py           # apply verdicts → bench.json `pass` (semantic);
        │                            #   rubric (run_eval.score_one) for compute/plot
        └─ gen_benchmark_page.py     # bench.json → /var/www/sigma/docs/benchmark/index.html
```

`finalize_benchmark.sh` is a watcher: while `gapfill_empties.py` runs it regenerates
the page on every bench.json change (incremental publish, ~90s), and on gapfill exit it
sends the report (`report_benchmark.py`) and **self-triggers** a grading session
(curl localhost:9357 → full Claude turn) because the adversarial judge needs the
Workflow tool, which bash can't invoke.

## Scoring model

- **Deterministic** cases (`compute_pure`, `compute_plot`, `vision_refine` numerics) →
  `run_eval.score_one` rubric (exact numbers/hashes/plot presence). Authoritative.
- **Semantic** cases (`rag_basic`, `definition`, `structural`, `out_of_scope`,
  `multi_hop`, `vision_refine` reasoning) → Claude-judge verdict in `judge_verdicts.jsonl`,
  keyed by `(case_id, sha1(answer))`, fallback `(case_id, model_short)`.

## Gotchas (learned the hard way)

1. **Stale-empty verdict bug.** A timed-out empty answer gets a cached verdict
   `pass=False, answer_sha1=da39a3ee…` (sha1 of ""). When gapfill replaces it with a
   real answer, `grade_hybrid`'s `(case_id, model_short)` fallback would apply that
   stale False to the recovered answer → wrong fail. **Fixed**: `verdict_for` rejects
   the `EMPTY_SHA1` fallback and forces a re-judge. `pending_judgements.py` uses the
   same sentinel so it actually surfaces recovered answers (else it reports 0).

2. **Grade only after gapfill fully completes.** `wf_adversarial_grade.js`'s DUMP does
   `json.load` on every bench.json. Running it while gapfill is mid-`write_text` on a
   model races → that case's judge agent errors. One authoritative grade at the end.

3. **`persist_verdicts.py` is idempotent** (dedup by `case_id+sha1+model_short`). Safe
   to run after partial/repeat grading — only genuinely-new answers get appended.

4. **Legitimacy critic must ACT, not just report.** The workflow's `auditReport` flags
   illegitimate scores (hallucination passed, correct answer nitpicked, infra-fail mass
   empties). Read it and fix flagged verdicts in `judge_verdicts.jsonl` BEFORE
   `grade_hybrid`.

5. **Restarts.** `bench_models.restart_and_wait` restarts only `sigma-assistant.service`
   (:8766), never claude-tg — so the trigger server (9357) is never disrupted.

5b. **Native-tool-calling artifact (gemma-3, 2026-06-10).** `server.py` uses native
   OpenRouter tool-calling and only benches models that advertise `tools`+`tool_choice`.
   Some models (gemma-3-12b/27b) PASS that filter but emit tool calls as plain text
   (`[search_textbook(...)]`) instead of structured `tool_calls` → the agent never runs
   them → fail on every tool case → artificially ~3-10%. This is the model's unreliable
   native tool-calling, NOT a parser bug we own. Don't build a text-fallback (rewards
   non-compliance). Flag or drop such models; their score is not a quality signal.

6b. **Raw `\(…\)` / `\[…\]` delimiters render as garbage (2026-06-10).** The site's
   `renderMarkdown` renders ONLY `$…$` and `$$…$$`. Math written with `\(…\)`/`\[…\]`
   passes through `escapeHtml` and the reader sees raw backslash-LaTeX — as bad as a
   `.katex-error`. `validate_render.js` can't see this (it only extracts `$`-formulas),
   so it reported `broken=0` and these answers scored as clean passes. **Found only by
   opening the live page in a browser and clicking cells** — the text-judge reads
   `\( x_0 = 2 \)` as legible intent and passes it. Three models emit this wholesale:
   seed-1.6-flash, ministral-8b, mistral-small (and stray cases in nova). **Fixed**:
   `render_gate._delim_counts` + `raw_render_defective` (rule: `raw>=2 and raw>=dollar`
   → the reader sees predominantly raw math → fail), wired into `grade_hybrid` next to
   the broken-formula gate. Calibrated against anchors: mistral-small newton (R=34,D=0,
   already failed by audit) and seed/ministral newton (central formula in `$$`, only
   prose `\(x_k\)` leaks → pass). Always run the live-page click-sweep after generating —
   on-disk `clean` count is blind to this class of defect.

6. **Page is served at `/benchmark/`** (301 from `/benchmark`); nginx injects the
   assistant widget scripts so served bytes > on-disk. Verify with `curl -L` or a
   real browser, and cache-bust (`?cb=…`).

## Turnkey final-grade sequence

```
cd /root/sigma_assistant/eval
python3 pending_judgements.py --json          # work-list (semantic answers w/o verdict)
# → Workflow tool, scriptPath wf_adversarial_grade.js  (save finalVerdicts to a file)
python3 persist_verdicts.py finalVerdicts.json
# → read auditReport, fix flagged verdicts in judge_verdicts.jsonl
python3 grade_hybrid.py
cd /root/sigma_assistant && python3 gen_benchmark_page.py
# → verify in browser (patchright): leaderboard+matrix+Pareto, no undefined/NaN
# → report_benchmark.py ; then safe_restart claude-tg (activates send_telegram_message)
# bench commit ONLY with Daniil's OK.
```

## Rubric grading (added 2026-06-11) — replaces binary judge for semantic cases

Daniil's directive: сложные вопросы под Claude-as-judge не должны грейдиться бинарно.
Вместо `pass:true/false` каждый семантический ответ грейдится **по рубрике из критериев**.

Artifacts:
- `rubrics.jsonl` — per semantic case: `criteria:[{id,text,weight,critical,auto?}]`.
  - `critical:true` → уровень `none` обнуляет весь балл (галлюцинация/нарушение/битый ключ).
  - `auto:"tool_expected"|"no_tools"|"image_present"` → уровень считается из данных
    прогона (tools/images), НЕ судьёй. Многомерность по запросу Даниила: суть, стиль,
    scope, инструменты (где нужно/не нужно), картинка.
- `rubric_score.py` — детерминированная агрегация: met=1.0/partial=0.5/none=0.0,
  score = Σ(w·level)/Σw, critical-none → 0. `PASS_THRESHOLD=0.6` для опц. бинарного вида.
- `wf_rubric_grade.js` — Workflow: judge→refute→audit, выдаёт уровни ПО КАЖДОМУ
  судейскому критерию (auto-критерии исключены из дампа судьи).
- `persist_rubric_verdicts.py` — finalVerdicts → `rubric_verdicts.jsonl` (key: case+sha1+model).
- `grade_rubric.py` — рубрично-осознанный грейдер: semantic → continuous rubric_score,
  deterministic → 0/1, рендер-гейты (broken/raw `\(…\)`) по-прежнему капят в 0.
  Пишет `rubric_score`, `rubric_capped`, `rubric_detail` на кейс; `avg_rubric_score` на модель.

Run order (after a fresh rerun): wf_rubric_grade.js → persist_rubric_verdicts.py
→ читать auditReport → grade_rubric.py → gen_benchmark_page.py → browser verify.
