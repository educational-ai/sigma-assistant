export const meta = {
  name: 'sigma-adversarial-grade',
  description: 'Adversarial Claude-as-judge grading of the Sigma benchmark until scores are fully legitimate',
  phases: [
    { title: 'Judge', detail: 'one judge per semantic case grades every model' },
    { title: 'Refute', detail: 'adversarial verifier challenges each verdict' },
    { title: 'Audit', detail: 'legitimacy critic hunts remaining illegitimate scores' },
  ],
}

// 15 semantic cases — graded by Claude (NOT by substring, NOT via OpenRouter).
// Deterministic cases (compute/plot/vision) are graded by code, not here.
const SEMANTIC = [
  'newton_kantorovich_history', 'newton_formula_recall', 'rsa_history',
  'fragment_explanation', 'math_latex_derivation',
  'definition_strong_convex', 'definition_superlinear', 'definition_perceptron',
  'structural_kantorovich_theorem', 'theorem_clt', 'outline_chapter',
  'out_of_scope_recipe', 'greeting_minimal', 'refuse_unknown_year',
  'multihop_newton_vs_gradient',
  // vision_refine reclassified to judge — the "0.5" substring was illegitimate
  // (any η<1 converges). Rubric fixed; judge grades diagnosis + working step.
  'vision_refine_diverging_sgd',
]

const VERDICTS_SCHEMA = {
  type: 'object',
  required: ['case_id', 'verdicts'],
  properties: {
    case_id: { type: 'string' },
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        required: ['model_short', 'pass', 'reason'],
        properties: {
          model_short: { type: 'string' },
          pass: { type: 'boolean' },
          reason: { type: 'string' },
        },
      },
    },
  },
}

const DUMP = (cid) =>
  `cd /root/sigma_assistant/eval && python3 -c "import json,glob
cases={json.loads(l)['id']:json.loads(l) for l in open('cases.jsonl') if l.strip()}
c=cases['${cid}']
print('QUESTION:',c['question']); print('RUBRIC:',c.get('rubric','')); print('='*80)
for d in sorted(glob.glob('bench_v*/*/bench.json')):
    b=json.load(open(d))
    for x in b['cases']:
        if x['id']=='${cid}':
            a=(x.get('answer') or '').replace(chr(10),' ')
            print('### MODEL', b['model'].split('/')[-1], '| tools=', x.get('tools'))
            print(a[:1400] if a else '<ПУСТО>'); print('-'*60)"`

const judgePrompt = (cid) => `Ты — строгий независимый экзаменатор учебника по оптимизации в ML. Грейдишь ОДИН вопрос бенчмарка по ВСЕМ моделям сразу (для единой калибровки).

Сначала прочитай вопрос, рубрику и ответы всех моделей — выполни этот bash:
${DUMP(cid)}

Затем по КАЖДОЙ модели вынеси вердикт СТРОГО по рубрике:
• Галлюцинация (выдуманный год/имя/факт, ложное подтверждение неверной предпосылки) = НЕЗАЧЁТ, даже если звучит уверенно.
• Нарушение явного ограничения рубрики (напр. «коротко <150 знаков», «без длинных простыней», «не выдумывай разделы») = НЕЗАЧЁТ.
• Честный отказ там, где рубрика требует отказа = ЗАЧЁТ.
• Пустой/обрезанный/мусорный ответ ('7', '0000', одно слово) = НЕЗАЧЁТ.
• LaTeX/формулы: ответы рендерятся через KaTeX. Если формула СЛОМАНА так, что KaTeX её не отрисует (незакрытые $/скобки \\(...\\), \\[...\\]; неизвестные макросы; \\text без скобок; сырой markdown вместо математики) И это ключевая формула ответа → НЕЗАЧЁТ (пользователь увидит красный мусор). Косметический изъян в формуле — отметь в reason, но не вали.
• Стиль: грубый кринж/непрофессиональность при формально верной сути (вода-простыни, нелепые обороты, эмодзи-спам, «как ИИ-ассистент…», самореклама) — отметь в reason; если стиль делает ответ негодным для учебника по оптимизации → НЕЗАЧЁТ. Лёгкая шероховатость стиля — НЕ повод валить.
Будь последователен между моделями: одинаковое качество → одинаковый вердикт. Приоритет: суть > битый LaTeX > стиль.

Верни структурой: case_id='${cid}' и verdicts — по записи на КАЖДУЮ модель из дампа (model_short ровно как в дампе), pass (bool), reason (кратко по-русски: суть + при наличии пометки про LaTeX/стиль).`

const refutePrompt = (cid, firstJSON) => `Ты — АДВЕРСАРИАЛЬНЫЙ верификатор. Твоя цель — найти и опровергнуть НЕВЕРНЫЕ вердикты первого судьи по вопросу '${cid}'.

Перечитай вопрос, рубрику и ВСЕ ответы:
${DUMP(cid)}

Вердикты первого судьи:
${firstJSON}

Перепроверь каждый враждебно:
• Где стоит ЗАЧЁТ — попробуй доказать, что это незачёт (скрытая галлюцинация, нарушенное ограничение рубрики, выдуманные факты, ответ не по существу, СЛОМАННЫЙ ключевой LaTeX, треш-стиль негодный для учебника).
• Где стоит НЕЗАЧЁТ — попробуй доказать, что ответ на самом деле верен (судья придрался к косметике стиля или к незначимому изъяну формулы, а суть корректна).
• Пустые/мусорные — всегда незачёт. Приоритет: суть > битый LaTeX > стиль.
Меняй вердикт ТОЛЬКО если уверен. Верни ОКОНЧАТЕЛЬНЫЕ согласованные вердикты в той же структуре (case_id, verdicts по всем моделям). В reason для изменённых добавь префикс «[изменено] ».`

phase('Judge')
log(`Adversarial grading of ${SEMANTIC.length} semantic cases across all benched models`)

// pipeline: judge → adversarial refute, per case, no barrier
const graded = await pipeline(
  SEMANTIC,
  (cid) => agent(judgePrompt(cid), { label: `judge:${cid}`, phase: 'Judge', schema: VERDICTS_SCHEMA }),
  (first, cid) =>
    agent(refutePrompt(cid, JSON.stringify(first?.verdicts || [])),
      { label: `refute:${cid}`, phase: 'Refute', schema: VERDICTS_SCHEMA })
)

const finalVerdicts = graded.filter(Boolean)

// ---- Audit: legitimacy critic over the whole graded set ----
phase('Audit')
const flat = finalVerdicts.flatMap((g) =>
  (g.verdicts || []).map((v) => `${g.case_id} | ${v.model_short} | ${v.pass ? 'PASS' : 'fail'} | ${v.reason}`)
)
const auditReport = await agent(
  `Ты — критик легитимности скоров бенчмарка. Вот итоговые вердикты Claude-судей по семантическим кейсам (case | model | verdict | reason):

${flat.join('\n')}

Также по желанию проверь детерминированные кейсы (compute_pure/compute_plot/vision_refine) — их грейдит код по точным числам/хэшам; убедись, что нет очевидных несоответствий:
cd /root/sigma_assistant/eval && python3 -c "import json,glob
for d in sorted(glob.glob('bench_v*/*/bench.json')):
 b=json.load(open(d))
 for c in b['cases']:
  if c['category'] in ('compute_pure','compute_plot','vision_refine'):
   print(b['model'].split('/')[-1], c['id'], 'pass=',c.get('pass'),'tools=',c.get('tools'))" | head -80

Найди ЛЮБУЮ нелегитимность: (1) галлюцинация, получившая зачёт; (2) корректный ответ, получивший незачёт по придирке; (3) непоследовательность между моделями на одинаковом качестве; (4) модель, оценённая на массе пустых/оборванных ответов (инфра-провал, а не качество); (5) детерминированный кейс с явно неверным грейдом. Верни сжатый список проблем с конкретикой (case+model+почему). Если всё чисто — верни 'ЧИСТО'.`,
  { label: 'legitimacy-critic', phase: 'Audit' }
)

return { finalVerdicts, auditReport }
