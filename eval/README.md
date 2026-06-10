# Sigma Assistant — Eval Test Set

Objective: track tool-use correctness, answer quality, and visual outputs over time as we iterate on the browser-side agent.

## Files

- `cases.jsonl` — golden test cases (one per line, see schema below).
- `run_eval.py` — runner: replays each case against the agent endpoint, captures full trace + final answer + any images, scores against golden.
- `reports/` — markdown reports per run, with embedded images.
- `logs/` — raw JSONL of every case run (for regression diff).

## Case schema

```json
{
  "id": "unique-slug",
  "category": "rag_basic | definition | compute_pure | compute_plot | multi_hop | vision_refine | structural | out_of_scope",
  "chapter_slug": "ch02_newton",         // optional: simulates reading context
  "fragment": "выделенный пользователем текст",  // optional
  "question": "Что задаёт студент",
  "history": [],                          // optional: prior turn(s)
  "expected_tools": [                    // ordered list; partial subset match
    {"name": "search", "args_contain": ["Канторович"]},
    {"name": "answer"}
  ],
  "expected_answer_contains": ["1975", "Нобел"],   // ALL substrings must be present
  "expected_answer_excludes": ["Шпильман"],         // NONE of these may appear
  "expected_visual": false,               // true if a plot/image is required
  "rubric": "плейн-текст для LLM-as-judge: что считается хорошим ответом"
}
```

## Categories

| Category | Что проверяет | Tools that should fire |
|---|---|---|
| `rag_basic` | Простой факт из учебника | `search` или `read_chapter` → answer |
| `definition` | Lookup определения по термину | `find_definition` → answer |
| `compute_pure` | Численный/символьный расчёт без графика | `python` → answer |
| `compute_plot` | Расчёт с визуализацией | `python` (с matplotlib) → answer + image |
| `multi_hop` | Сопоставление 2+ глав | 2-3× search/read → answer |
| `vision_refine` | Агент строит график, смотрит, корректирует | `python` → vision-обратная связь → `python` снова → answer |
| `structural` | Lookup теоремы/леммы по имени | `find_theorem` → answer |
| `out_of_scope` | Вопрос не из учебника | Любые tools или нет, но answer должен явно отказать |

## Scoring

1. **Tool order correctness** (binary per expected tool): был ли вызван tool с substring args.
2. **Answer substring match** (binary): все `expected_answer_contains` присутствуют, ни одного `expected_answer_excludes`.
3. **LLM-as-judge** (1-5 score): дешёвая модель оценивает соответствие rubric. Промпт включает question + answer + rubric.
4. **Visual presence** (binary): если `expected_visual` — был ли возвращён image_url с непустым PNG.

Aggregate: pass rate per category + overall composite score.

## Running

```bash
cd /root/sigma_assistant/eval
OPENROUTER_API_KEY=... python3 run_eval.py --cases cases.jsonl --report reports/$(date +%F).md
```

## Cron

Weekly run on Sunday 03:00 MSK → report sent to TG (DIRECT). See `scripts/eval_sigma_weekly.sh`.
