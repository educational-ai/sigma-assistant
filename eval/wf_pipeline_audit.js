export const meta = {
  name: 'pipeline-audit',
  description: 'Адверсариальный аудит ВСЕГО бенч-пайплайна Sigma: агент, раннер, грейдинг, датасет, оркестрация, репортинг',
  whenToUse: 'Найти места, где пайплайн врёт, теряет данные или оверфитится — до выпуска v2',
  phases: [
    { title: 'Attack', detail: 'аудиторы атакуют каждый компонент' },
    { title: 'Verify', detail: 'каждая находка воспроизводится или отбрасывается' },
    { title: 'Synthesize', detail: 'приоритизированный отчёт' },
  ],
}

const ROOT = '/root/sigma_assistant'
const COMMON = `
Ты — адверсариальный аудитор бенч-пайплайна ИИ-ассистента учебника Σ.
Код и данные на диске, читай что нужно: ${ROOT} (server.py, bench_models.py,
gen_benchmark_page.py, eval/run_eval.py, eval/grade_hybrid.py, eval/regrade.py,
eval/rubric_score.py, eval/cases.jsonl, eval/rubrics.jsonl, eval/bench_v1/*/,
eval/llm_log_dev.jsonl, eval/judge_verdicts.jsonl) и фронтенд-агент
/var/www/sigma-dev/docs/assistant/assistant.js.
Задача НЕ критиковать ответы конкретных моделей — задача найти, где сама СИСТЕМА:
(а) врёт (числа/страница/оценки не соответствуют реальности),
(б) теряет невоспроизводимые данные,
(в) несправедлива к моделям или оверфитится на одно семейство/один харнесс,
(г) хрупкая (упадёт молча, оставит грязь).
Каждая находка обязана иметь ДОКАЗАТЕЛЬСТВО: файл:строка, команда с выводом,
конкретный кейс из данных. ВСЁ, что можно проверить ВИЗУАЛЬНО — проверяй визуально:
скриншоты в eval/bench_v1/*/<case>.png и figs/*.png открывай Read-ом (ты видишь
картинки); живые страницы (https://sigma.fmin.xyz/benchmark/, https://sigmadev.fmin.xyz)
скринь headless-браузером и смотри глазами:
  timeout 60 python3 -c "from patchright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); pg=b.new_page(); pg.goto('URL', wait_until='networkidle'); pg.screenshot(path='/tmp/audit_<имя>.png', full_page=True); b.close(); p.stop()"
Вывод «по коду должно работать» без визуальной сверки = невыполненная работа.
Гипотезы без проверки не возвращай. Максимум 6 находок — только то, за что готов ручаться.`

const FINDING_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          severity: { type: 'string', enum: ['critical', 'major', 'minor'] },
          component: { type: 'string' },
          evidence: { type: 'string', description: 'файл:строка / команда+вывод / кейс' },
          impact: { type: 'string', description: 'что именно исказится или потеряется' },
          fix: { type: 'string', description: 'минимальный конкретный фикс' },
        },
        required: ['title', 'severity', 'component', 'evidence', 'impact', 'fix'],
      },
    },
  },
  required: ['findings'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    real: { type: 'boolean' },
    proof: { type: 'string', description: 'воспроизведение (команда+вывод) или опровержение' },
    severity_adjusted: { type: 'string', enum: ['critical', 'major', 'minor', 'not-a-bug'] },
  },
  required: ['real', 'proof', 'severity_adjusted'],
}

const AUDITORS = [
  { key: 'agent', prompt: `${COMMON}
Компонент: САМ АГЕНТ — системный промпт (server.py, ищи большой русский промпт) +
агентный цикл фронтенда (/var/www/sigma-dev/docs/assistant/assistant.js: TOOLS,
streamCompletion, tool-протокол, vision-фидбек после python, обработка ошибок,
таймаут Pyodide). Атакуй архитектуру: где цикл теряет/искажает ответы моделей
не по их вине (протокольные DNF, обрывы, двойной рендер, потерянный tool_call);
где промпт делает бенч несправедливым (заточен под один стиль моделей?);
где вижн-цикл может не сработать. Смотри реальные примеры в eval/llm_log_dev.jsonl.` },
  { key: 'runner', prompt: `${COMMON}
Компонент: РАННЕР eval/run_eval.py. Атакуй честность измерения: захват ответа из DOM
(dataset.raw vs innerText), figures/скриншоты, ретраи пустых ответов, адаптивный
таймаут (ASK_TIMEOUT_S/ASK_HARD_CEIL_S), RECYCLE_EVERY и смерть браузера, гонки
(reset между кейсами, остаточное состояние чата), t_start/t_end окна. Где раннер
может приписать модели чужой провал или потерять её успех? Сверь с реальными
данными: eval/bench_v1/*/results.jsonl, run.log.` },
  { key: 'grading', prompt: `${COMMON}
Компонент: ГРЕЙДИНГ — eval/run_eval.py::score_one (+_is_garbage, _contains, стемминг),
eval/regrade.py, eval/grade_hybrid.py (+judge_verdicts.jsonl кэш по sha1),
eval/rubric_score.py, взаимодействие сабстринг↔судья. Атакуй: классы ложных
зачётов/незачётов (проверь на реальных ответах из bench_v1!), протухание оценок
(инцидент: голый regrade затёр судейские вердикты), sha1-кэш (ответ чуть изменился —
вердикт потерян), рубрики (веса/critical адекватны?). Где цепочка непоследовательна:
одни и те же данные разными путями дают разный pass?` },
  { key: 'dataset', prompt: `${COMMON}
Компонент: ДАТАСЕТ eval/cases.jsonl (29 кейсов) + eval/rubrics.jsonl. Атакуй как
психометрик: жёсткие сабстринг-ключи (найди кейсы, где верный ответ другими словами
провалится — проверь на реальных ответах моделей из bench_v1), двусмысленные вопросы,
несбалансированные категории, кейсы-дубли, отсутствующие рубрики, чрезмерная
завязанность на главу ch02_newton (оверфит на одну главу учебника?). Что сломается
при масштабировании на 81 кейс (PR команды)?` },
  { key: 'orchestration', prompt: `${COMMON}
Компонент: ОРКЕСТРАЦИЯ bench_models.py + server.py (прокси/логирование). Атакуй:
атрибуция стоимости по окнам времени (что если два запроса пересеклись? фоновые
юзеры сайта попадают в цену модели?), гигачат-курс ЦБ и рублёвые тарифы (сверь
с актуальным прайсом Сбера через веб если можешь, иначе пометь непроверенным),
восстановление .env по сигналам, skip/--force логика, версии bench_v*, полнота
llm_log (ротация, потери при падении), SIGTERM посреди записи bench.json.` },
  { key: 'reporting', prompt: `${COMMON}
Компонент: РЕПОРТИНГ gen_benchmark_page.py + страница /benchmark (можешь открыть
https://sigma.fmin.xyz/benchmark/ и data.json curl-ом). Атакуй правдивость: case_state
(broken/DNF-логика — где реальный провал модели маскируется под DNF и наоборот),
сводные числа vs сырые кейсы, heatmap, стоимость на странице vs bench.json, свежесть
(страница может показывать смесь старых и новых прогонов как одно целое — это честно?),
пометки об утерянных артефактах. Где читатель страницы получит ЛОЖНЫЙ вывод?` },
]

phase('Attack')
log(`${AUDITORS.length} аудиторов атакуют пайплайн`)

const found = await pipeline(
  AUDITORS,
  (a) => agent(a.prompt, { schema: FINDING_SCHEMA, phase: 'Attack', label: `attack:${a.key}`, effort: 'high' })
    .then((r) => (r ? r.findings.map((f) => ({ ...f, auditor: a.key })) : [])),
  (findings) => parallel((findings || []).map((f) => () =>
    agent(`${COMMON}
Проверь чужую находку аудита. Начни со скепсиса: попробуй ОПРОВЕРГНУТЬ её,
воспроизведя доказательство самостоятельно (команды, файлы, данные).
Находка: ${JSON.stringify(f)}
real=true только если сам воспроизвёл суть. Заодно скорректируй severity по
реальному влиянию на честность бенча.`,
      { schema: VERDICT_SCHEMA, phase: 'Verify', model: 'opus', label: `verify:${f.auditor}:${f.title.slice(0, 40)}` })
      .then((v) => ({ ...f, verdict: v })))),
)

const confirmed = found.flat().filter(Boolean).filter((f) => f.verdict && f.verdict.real && f.verdict.severity_adjusted !== 'not-a-bug')
log(`подтверждено находок: ${confirmed.length}`)

phase('Synthesize')
const report = await agent(`Ты — главный аудитор бенч-пайплайна Σ. Вот подтверждённые находки
(каждая воспроизведена независимым проверяющим):
${JSON.stringify(confirmed, null, 1)}
Собери минималистичный отчёт на русском в Markdown:
1) TL;DR — 3-5 строк: чем пайплайн болен системно;
2) таблица находок по убыванию severity (title / компонент / влияние / фикс);
3) «Не чинить»: что выглядит багом, но им не является (если есть);
4) порядок работ: какие 3 фикса дают максимум честности бенча на рубль.
Без воды, каждая строка должна быть действием или фактом.`,
  { phase: 'Synthesize', label: 'report', effort: 'high' })

return { confirmed_count: confirmed.length, findings: confirmed, report }
