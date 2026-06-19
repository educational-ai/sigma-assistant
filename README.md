# Sigma Assistant + Benchmark

Бэкенд **ИИ-ассистента** учебника [Sigma](https://sigma.fmin.xyz) и **бенчмарк** агента.
Это приватная часть проекта Sigma (учебник — репо `sigma`, ассистент и бенч — здесь).

## Что внутри

- **Ассистент** (`server.py`, `:8766`) — agentic-RAG над учебником: прокси к OpenRouter,
  нативные tool_calls (`/api/textbook/*` + исполнение python в браузере через Pyodide),
  цикл ≤8 шагов. Модель задаётся `SIGMA_MODEL` в `.env`.
- **Бенчмарк** (`eval/`) — как один и тот же агент работает при смене только LLM.
  Публичная витрина: [sigma.fmin.xyz/benchmark](https://sigma.fmin.xyz/benchmark).

## Документация бенча (начни отсюда)

| Файл | О чём |
|------|-------|
| [`eval/README.md`](eval/README.md) | Тест-сет: схема кейса, 8 категорий, что значит tool/answer/visual match |
| [`eval/GRADING_PIPELINE.md`](eval/GRADING_PIPELINE.md) | Как лидерборд получает оценки: data flow, scoring model, гочи, rubric grading |
| `eval/cases.jsonl` | Золотые тест-кейсы (29 + 7 extra) |
| `eval/bench/<model>/bench.json` | Прогон по модели: ответы + оценки |
| [`eval/audit_grader_misses.py`](eval/audit_grader_misses.py) | Pre-filter: зачтённые кейсы с красным флагом (питон не вызван / картинка не приложена / поиск не сделан / обрубок) |

## Как устроена оценка (коротко)

Гибрид: **LLM-судья** (семантические категории) + **рубрика** (`score_one`: числа/графики/тулы) +
**render-gate** (формулы реально рендерятся?). Подробно — `eval/GRADING_PIPELINE.md`.

⚠️ Известные проблемы оценки (см. задачи QA на доске): судья и рубрика расходятся на ~12%
кейсов; render-gate штрафует модель за `\(…\)`, которые не рендерит сайт (вина сайта, не модели).

## Как гонять

```bash
# прогон одной модели против ЖИВОГО ассистента (нужен OPENROUTER_API_KEY в .env)
python3 bench_models.py            # см. шапку файла
# пересчитать оценки из сохранённых ответов БЕЗ перепрогона моделей
python3 eval/regrade.py
# собрать публичную страницу
python3 gen_benchmark_page.py
```

Инвариант: тестируется **ровно тот агент, что на сайте** — меняется только `SIGMA_MODEL`.
Стоимость — фактические списания OpenRouter (`usage.cost`), не оценка.

> Коммит/пуш бенч-данных — только с ОК Даниила.
