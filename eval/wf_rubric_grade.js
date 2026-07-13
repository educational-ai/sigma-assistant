export const meta = {
  name: 'sigma-rubric-grade',
  description: 'Rubric-based Claude-as-judge grading of the Sigma benchmark — per-criterion levels (met/partial/none), not binary',
  phases: [
    { title: 'Judge', detail: 'one judge per semantic case grades every model per criterion' },
    { title: 'Refute', detail: 'adversarial verifier challenges each per-criterion verdict' },
    { title: 'Audit', detail: 'legitimacy critic hunts inconsistent / illegitimate criterion levels' },
  ],
}

// Semantic cases graded by Claude per rubric criterion. Deterministic categories
// (compute/plot) and the AUTO criteria (tools/image) are scored by code, not here.
const SEMANTIC = [
  'newton_kantorovich_history', 'newton_formula_recall', 'rsa_history',
  'fragment_explanation', 'math_latex_derivation',
  'definition_strong_convex', 'definition_superlinear', 'definition_perceptron',
  'structural_kantorovich_theorem', 'theorem_clt', 'outline_chapter',
  'out_of_scope_recipe', 'greeting_minimal', 'refuse_unknown_year',
  'multihop_newton_vs_gradient', 'vision_refine_diverging_sgd',
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
        required: ['model_short', 'criteria'],
        properties: {
          model_short: { type: 'string' },
          criteria: {
            type: 'array',
            items: {
              type: 'object',
              required: ['id', 'level'],
              properties: {
                id: { type: 'string' },
                level: { type: 'string', enum: ['met', 'partial', 'none'] },
                note: { type: 'string' },
              },
            },
          },
        },
      },
    },
  },
}

// Dump question + JUDGED criteria (auto excluded) + every model's answer.
const DUMP = (cid) =>
  `cd /root/sigma_assistant/eval && python3 -c "import json,glob
cases={json.loads(l)['id']:json.loads(l) for l in open('cases.jsonl') if l.strip()}
rub={json.loads(l)['case_id']:json.loads(l)['criteria'] for l in open('rubrics.jsonl') if l.strip()}
c=cases['${cid}']
print('QUESTION:',c['question'])
print('КРИТЕРИИ ДЛЯ ОЦЕНКИ (только эти id, auto-критерии инструментов/картинки НЕ оценивай — их считает код):')
for cr in rub['${cid}']:
    if cr.get('auto'): continue
    print(' -',cr['id'],'(вес',cr['weight'],('КРИТИЧ' if cr['critical'] else 'обычн')+'):',cr['text'])
print('='*80)
for d in sorted(glob.glob('bench_v*/*/bench.json')):
    b=json.load(open(d))
    for x in b['cases']:
        if x['id']=='${cid}':
            a=(x.get('answer') or '').replace(chr(10),' ')
            print('### MODEL', b['model'].split('/')[-1], '| tools=', x.get('tools'))
            print(a[:1600] if a else '<ПУСТО>'); print('-'*60)"`

const judgePrompt = (cid) => `Ты — строгий независимый экзаменатор учебника по оптимизации в ML. Грейдишь ОДИН вопрос бенчмарка по ВСЕМ моделям сразу (для единой калибровки), по СТРУКТУРИРОВАННОЙ РУБРИКЕ — не бинарно, а по каждому критерию отдельно.

Сначала прочитай вопрос, критерии и ответы всех моделей:
${DUMP(cid)}

Затем по КАЖДОЙ модели оцени КАЖДЫЙ судейский критерий уровнем:
• "met" — критерий полностью выполнен;
• "partial" — частично (затронут, но неполно/с оговорками/мелкая ошибка);
• "none" — не выполнен / нарушен / галлюцинация по этому критерию.
Правила:
• Галлюцинация (выдуманный год/имя/факт, ложное подтверждение неверной предпосылки) → "none" на соответствующем критерии, даже если звучит уверенно.
• Пустой/обрезанный/мусорный ответ ('<ПУСТО>', '7', одно слово) → все критерии "none".
• LaTeX: ответы рендерятся через KaTeX. Если СЛОМАНА ключевая формула (KaTeX не отрисует) → критерий про неё "none" или "partial". Косметика формулы — отметь в note.
• Стиль (критерий style): грубый кринж/вода/эмодзи-спам/«как ИИ-ассистент…»/самореклама → "none" или "partial"; лёгкая шероховатость → "met" с пометкой.
Будь последователен между моделями: одинаковое качество → одинаковый уровень.

Верни структурой: case_id='${cid}' и verdicts — по записи на КАЖДУЮ модель из дампа: model_short (ровно как в дампе), criteria — массив {id (точно как id критерия), level (met|partial|none), note (очень кратко по-русски)}. Оцени ТОЛЬКО перечисленные судейские критерии.`

const refutePrompt = (cid, firstJSON) => `Ты — АДВЕРСАРИАЛЬНЫЙ верификатор рубричных оценок по вопросу '${cid}'. Цель — найти и исправить НЕВЕРНЫЕ уровни первого судьи.

Перечитай вопрос, критерии и ВСЕ ответы:
${DUMP(cid)}

Уровни первого судьи (по критериям, по моделям):
${firstJSON}

Перепроверь каждый уровень враждебно:
• Где стоит "met" — попробуй доказать "partial"/"none" (скрытая галлюцинация, неполнота, нарушенное ограничение, сломанный ключевой LaTeX, треш-стиль).
• Где "none"/"partial" — попробуй доказать, что критерий на самом деле выполнен (судья придрался к косметике, а суть верна).
• Пустые/мусорные — все критерии "none".
Меняй уровень ТОЛЬКО если уверен. Будь последователен между моделями. Верни ОКОНЧАТЕЛЬНЫЕ согласованные уровни в той же структуре (case_id, verdicts по всем моделям, criteria по всем судейским id). В note изменённых добавь префикс «[изменено] ».`

phase('Judge')
log(`Rubric grading of ${SEMANTIC.length} semantic cases × all benched models (per-criterion)`)

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
  (g.verdicts || []).map((v) =>
    `${g.case_id} | ${v.model_short} | ` +
    (v.criteria || []).map((cr) => `${cr.id}=${cr.level}`).join(' ')))

const auditReport = await agent(
  `Ты — критик легитимности рубричных оценок бенчмарка. Вот итоговые уровни Claude-судей по семантическим кейсам (case | model | критерий=уровень …):

${flat.join('\n')}

Найди ЛЮБУЮ нелегитимность: (1) галлюцинация, получившая "met" на критерии корректности; (2) корректный ответ, заниженный на "none" по придирке; (3) непоследовательность между моделями — одинаковое качество, разные уровни на одном критерии; (4) модель, оценённая на массе пустых/оборванных ответов (инфра-провал). Верни сжатый список проблем (case+model+критерий+почему). Если всё чисто — верни 'ЧИСТО'.`,
  { label: 'legitimacy-critic', phase: 'Audit' }
)

return { finalVerdicts, auditReport }
