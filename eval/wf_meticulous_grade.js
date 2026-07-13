export const meta = {
  name: 'meticulous-grade',
  description: 'Въедливый адверсариальный грейдинг ответов бенча: судья по факторам → оппонент обязан опровергнуть → арбитр при расхождении',
  whenToUse: 'Судить семантические кейсы бенча Sigma (pending_judgements.py --json → items)',
  phases: [
    { title: 'Judge', detail: 'детальный вердикт по факторам и рубрике' },
    { title: 'Refute', detail: 'оппонент атакует вердикт (пересчёт, файлы, трейс)' },
    { title: 'Tiebreak', detail: 'арбитр при расхождении' },
  ],
}

// items вшиваются сюда генератором (Workflow args ненадёжен для массивов):
//   python3 eval/build_judge_items.py  — обновляет блок между маркерами
// __ITEMS_START__
const ITEMS = [{"model_dir": "gigachat_2", "model_short": "GigaChat-2", "case_id": "newton_kantorovich_history", "answer_sha1": "d64190ab58f62ed6b64e884442ca66613e60d37b"}, {"model_dir": "gigachat_2", "model_short": "GigaChat-2", "case_id": "newton_formula_recall", "answer_sha1": "bdc0e5fcdd6c607c5a6f6f3e2e75de95c1784d66"}, {"model_dir": "gigachat_2", "model_short": "GigaChat-2", "case_id": "structural_kantorovich_theorem", "answer_sha1": "e97d31e8365d46b9080b90a38b8f86005ec5e5e7"}, {"model_dir": "gigachat_2", "model_short": "GigaChat-2", "case_id": "rsa_history", "answer_sha1": "b3704e0a707618112828da1b267eae1f05ad8cf7"}, {"model_dir": "gigachat_2", "model_short": "GigaChat-2", "case_id": "greeting_minimal", "answer_sha1": "1e51ca798f4ec1f6e64b33ff140721ad54a13d23"}, {"model_dir": "gigachat_2", "model_short": "GigaChat-2", "case_id": "definition_superlinear", "answer_sha1": "79254d18e6060a0069bb873ecb49551e23722f30"}, {"model_dir": "gigachat_2", "model_short": "GigaChat-2", "case_id": "theorem_clt", "answer_sha1": "56a21751a8524871204a81509fc3ff206c8b2f58"}, {"model_dir": "gigachat_2", "model_short": "GigaChat-2", "case_id": "fragment_explanation", "answer_sha1": "bdc4cf0135cfb54d8537121958d2dd15a9f07c32"}, {"model_dir": "gigachat_2", "model_short": "GigaChat-2", "case_id": "outline_chapter", "answer_sha1": "75762fa5635b8f835ec7a2b67cf9e80141192ea5"}, {"model_dir": "gigachat_2", "model_short": "GigaChat-2", "case_id": "math_latex_derivation", "answer_sha1": "d7548f4421c74f53baf3b68a923c260e71c55d70"}, {"model_dir": "gigachat_2", "model_short": "GigaChat-2", "case_id": "definition_perceptron", "answer_sha1": "3a1587da18c6603b795f78f7d9967b60ce603259"}, {"model_dir": "gigachat_2", "model_short": "GigaChat-2", "case_id": "refuse_unknown_year", "answer_sha1": "de49e456429257e3583105026bba1c978258d37f"}, {"model_dir": "gigachat_2_max", "model_short": "GigaChat-2-Max", "case_id": "newton_kantorovich_history", "answer_sha1": "d9fc18baf3ec27953856979dc4ba6b6af5e74601"}, {"model_dir": "gigachat_2_max", "model_short": "GigaChat-2-Max", "case_id": "newton_formula_recall", "answer_sha1": "e6c7bedc441d08170c431ac744ff1dc59cd5d2c3"}, {"model_dir": "gigachat_2_max", "model_short": "GigaChat-2-Max", "case_id": "definition_strong_convex", "answer_sha1": "00538602d83c7e0cc36eb0583cf94aadfd1e7717"}, {"model_dir": "gigachat_2_max", "model_short": "GigaChat-2-Max", "case_id": "multihop_newton_vs_gradient", "answer_sha1": "bf0feabe045ac8cb80f61c5102f98585752cb3ce"}, {"model_dir": "gigachat_2_max", "model_short": "GigaChat-2-Max", "case_id": "structural_kantorovich_theorem", "answer_sha1": "2052e10a15adfeb7fa7e60c6709fc9962b8703cf"}, {"model_dir": "gigachat_2_max", "model_short": "GigaChat-2-Max", "case_id": "vision_refine_diverging_sgd", "answer_sha1": "ce3640398772e288717b09c69e661ba7d76cf40c"}, {"model_dir": "gigachat_2_max", "model_short": "GigaChat-2-Max", "case_id": "out_of_scope_recipe", "answer_sha1": "76d5e4a6fcf89146b69d8e244b6de7041967cfed"}, {"model_dir": "gigachat_2_max", "model_short": "GigaChat-2-Max", "case_id": "rsa_history", "answer_sha1": "a9c58cb6bc93ae92ec24ce8512c37ed331522c7c"}, {"model_dir": "gigachat_2_max", "model_short": "GigaChat-2-Max", "case_id": "greeting_minimal", "answer_sha1": "8ec595835f49ac02a94f363011be099b31a94b69"}, {"model_dir": "gigachat_2_max", "model_short": "GigaChat-2-Max", "case_id": "definition_superlinear", "answer_sha1": "2a3fa827438aa2d61dcb43b1f59806b64a948b8f"}, {"model_dir": "gigachat_2_max", "model_short": "GigaChat-2-Max", "case_id": "theorem_clt", "answer_sha1": "8497d9fb9de68077261d014830189575a9cc2879"}, {"model_dir": "gigachat_2_max", "model_short": "GigaChat-2-Max", "case_id": "fragment_explanation", "answer_sha1": "8826751562bafff281f9eadd3a67610439731438"}, {"model_dir": "gigachat_2_max", "model_short": "GigaChat-2-Max", "case_id": "outline_chapter", "answer_sha1": "13c2eff6d094c1c3768b534bccbb4fbcf3994cd4"}, {"model_dir": "gigachat_2_max", "model_short": "GigaChat-2-Max", "case_id": "math_latex_derivation", "answer_sha1": "c2c059056804b51756b6d451899eeb6a5a938c49"}, {"model_dir": "gigachat_2_max", "model_short": "GigaChat-2-Max", "case_id": "definition_perceptron", "answer_sha1": "6861988774cf7a8da87b20aa57432130482f7da8"}, {"model_dir": "gigachat_2_max", "model_short": "GigaChat-2-Max", "case_id": "refuse_unknown_year", "answer_sha1": "cf7764c052fafd3ab32d743f4bc5f33bd99b4596"}, {"model_dir": "gigachat_2_pro", "model_short": "GigaChat-2-Pro", "case_id": "newton_kantorovich_history", "answer_sha1": "5f67671ea7d19ef1da3d287f91f10e3d00b40b0d"}, {"model_dir": "gigachat_2_pro", "model_short": "GigaChat-2-Pro", "case_id": "newton_formula_recall", "answer_sha1": "9e9cfe5daabcab7788331bdd4bae8ee541453fae"}, {"model_dir": "gigachat_2_pro", "model_short": "GigaChat-2-Pro", "case_id": "definition_strong_convex", "answer_sha1": "2479936e511cd754a7d32de0188f39621107321d"}, {"model_dir": "gigachat_2_pro", "model_short": "GigaChat-2-Pro", "case_id": "multihop_newton_vs_gradient", "answer_sha1": "676a4cd8183bc306a598268759e51ec6f20540d2"}, {"model_dir": "gigachat_2_pro", "model_short": "GigaChat-2-Pro", "case_id": "structural_kantorovich_theorem", "answer_sha1": "879afed9cf9626e36e419484c73748a68d3abd41"}, {"model_dir": "gigachat_2_pro", "model_short": "GigaChat-2-Pro", "case_id": "vision_refine_diverging_sgd", "answer_sha1": "05bc37f28c1c6ab8675484c01b1bfb0082abdc38"}, {"model_dir": "gigachat_2_pro", "model_short": "GigaChat-2-Pro", "case_id": "out_of_scope_recipe", "answer_sha1": "a689b7bd4f8a418a59ee9a38d3f252898f097f73"}, {"model_dir": "gigachat_2_pro", "model_short": "GigaChat-2-Pro", "case_id": "rsa_history", "answer_sha1": "057a26f4a4d83b2104c7fed0fee1942bd6757131"}, {"model_dir": "gigachat_2_pro", "model_short": "GigaChat-2-Pro", "case_id": "greeting_minimal", "answer_sha1": "794aaadf38ba56921d6eeb8904f9ee5f9a47bd56"}, {"model_dir": "gigachat_2_pro", "model_short": "GigaChat-2-Pro", "case_id": "definition_superlinear", "answer_sha1": "eff091170aed4ad6d56e9ac3aab67b02692b978e"}, {"model_dir": "gigachat_2_pro", "model_short": "GigaChat-2-Pro", "case_id": "theorem_clt", "answer_sha1": "3e0e14d80c3e932f383ec7d6946ffd0db852a325"}, {"model_dir": "gigachat_2_pro", "model_short": "GigaChat-2-Pro", "case_id": "fragment_explanation", "answer_sha1": "478347909b4c155e77916dc195d74e54cbb29ab6"}, {"model_dir": "gigachat_2_pro", "model_short": "GigaChat-2-Pro", "case_id": "outline_chapter", "answer_sha1": "721413aef6a505162f19847bee5adf26159651cd"}, {"model_dir": "gigachat_2_pro", "model_short": "GigaChat-2-Pro", "case_id": "math_latex_derivation", "answer_sha1": "545bb1e58fb0a081c5700db339435659232f9c52"}, {"model_dir": "gigachat_2_pro", "model_short": "GigaChat-2-Pro", "case_id": "definition_perceptron", "answer_sha1": "d81041c6aa14b3b51592664481e5d533f65485f5"}, {"model_dir": "gigachat_2_pro", "model_short": "GigaChat-2-Pro", "case_id": "refuse_unknown_year", "answer_sha1": "3c73a594fabfc7d1f14f53ff2093ce6a3360e4cd"}, {"model_dir": "google_gemini_2_5_flash_lite", "model_short": "gemini-2.5-flash-lite", "case_id": "definition_strong_convex", "answer_sha1": "fe818672dec1ddda563cc22bc6b28e7b9445a8b7"}, {"model_dir": "google_gemini_2_5_flash_lite", "model_short": "gemini-2.5-flash-lite", "case_id": "structural_kantorovich_theorem", "answer_sha1": "be8497abb9284f9335d56302d72e018807e7ca8e"}, {"model_dir": "google_gemini_2_5_flash_lite", "model_short": "gemini-2.5-flash-lite", "case_id": "definition_superlinear", "answer_sha1": "10236a90f3d4f6a14a080fd735778e915d969b74"}, {"model_dir": "google_gemini_2_5_flash_lite", "model_short": "gemini-2.5-flash-lite", "case_id": "definition_perceptron", "answer_sha1": "6aa0e7b56fc2e552a8698e25aa49772846fa0e35"}]
// __ITEMS_END__
if (!ITEMS.length) return { error: 'нет items — прогони python3 eval/build_judge_items.py' }

const BENCH = '/root/sigma_assistant/eval/bench_v1'
const EVAL = '/root/sigma_assistant/eval'

const artefacts = (it) => `
Артефакты кейса (всё уже на диске, читай сам):
1. Кейс целиком (вопрос, ответ модели, трейс с ПОЛНЫМИ вызовами и ответами тулзов, счётчик картинок):
   python3 -c "import json,sys;d=json.load(open('${BENCH}/${it.model_dir}/bench.json'));c=[x for x in d['cases'] if x['id']=='${it.case_id}'][0];json.dump(c,sys.stdout,ensure_ascii=False,indent=1)"
2. Голден-ожидания кейса:
   python3 -c "import json;print([json.dumps(c,ensure_ascii=False) for c in map(json.loads,open('${EVAL}/cases.jsonl')) if c['id']=='${it.case_id}'][0])"
3. Рубрика с весами и critical-флагами (может отсутствовать):
   grep '"case_id": "${it.case_id}"' ${EVAL}/rubrics.jsonl | head -1
4. Графики агента, если были: ls ${BENCH}/${it.model_dir}/figs/${it.case_id}_*.png (открывай Read — ты видишь картинки)
5. Скрин ответа на живой странице: ${BENCH}/${it.model_dir}/${it.case_id}.png (Read)`

const FACTORS = `
Факторы (по каждому — ok/partial/fail + одна строка почему, с цитатой/фактом; судим ВСЮ работу агента, не только текст):
- task: вопрос разложи на под-требования; КАЖДОЕ выполнено? (просили график и подбор шага — есть ОБА?)
- facts: числа/годы/имена/формулы верны? Вычисления ПЕРЕСЧИТАЙ сам через python, не верь на слово.
- grounding: ключевые утверждения подтверждены ответами тулзов из трейса (или математикой)? Заявил «построил график» — картинка реально существует? Заявил «нашёл в учебнике» — это есть в result тула?
- agent: работа агента как агента — стратегия вызовов по трейсу: нужные ли тулзы, в разумном ли порядке, нет ли бессмысленных повторов одного запроса или лишних вызовов; при пустом поиске — переформулировал? при нехватке сниппета — дочитал главу read_chapter? ошибка python → исправил и перезапустил, не бросил? разумное ли время (elapsed)?
- visual: КАЖДЫЙ график из figs/ открой Read-ом и оцени глазами: соответствует ли задаче то, что реально нарисовано; подписаны ли оси/легенда; нет ли пустого/сломанного рисунка. Нет графиков при задаче «построй» = fail.
- site: открой скрин страницы (<case>.png) и посмотри как читатель: формулы отрендерены (не сырой LaTeX, не красные ошибки), вёрстка ответа в виджете не поехала, ответ выглядит опрятно.
- rubric: если рубрика есть — пройди criteria по одному: met/partial/none. critical-критерий none = незачёт всего ответа.
- form: весь ответ по-русски (куски англ/кит = дефект), формулы только $..$/$$..$$, нет лика служебного текста (<thinking>, сырой tool-call JSON, scaffold-фразы).
- invariant: категорийный инвариант — out_of_scope: вежливый отказ БЕЗ выдуманного ответа; definition/structural: определение/теорема соответствует учебнику (сверь с result тула); vision_refine: в трейсе виден цикл график→просмотр→корректировка; compute_*: результат получен ВЫПОЛНЕННЫМ кодом, не сочинён.`

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    pass: { type: 'boolean' },
    score: { type: 'number', description: '0..1 взвешенная оценка' },
    factors: {
      type: 'object',
      properties: {
        task: { type: 'string' }, facts: { type: 'string' }, grounding: { type: 'string' },
        agent: { type: 'string' }, visual: { type: 'string' }, site: { type: 'string' },
        rubric: { type: 'string' }, form: { type: 'string' }, invariant: { type: 'string' },
      },
      required: ['task', 'facts', 'grounding', 'agent', 'visual', 'site', 'rubric', 'form', 'invariant'],
    },
    critical_defects: { type: 'array', items: { type: 'string' } },
    reason: { type: 'string', description: 'итог одной строкой' },
  },
  required: ['pass', 'score', 'factors', 'critical_defects', 'reason'],
}

const REFUTE_SCHEMA = {
  type: 'object',
  properties: {
    agrees: { type: 'boolean' },
    corrected_pass: { type: 'boolean', description: 'каким должен быть pass по-твоему' },
    argument: { type: 'string', description: 'конкретное доказательство: цитата/пересчёт/файл' },
  },
  required: ['agrees', 'corrected_pass', 'argument'],
}

const judgePrompt = (it) => `Ты — въедливый судья бенчмарка ИИ-ассистента учебника Σ (русский школьно-вузовский учебник).
Оцениваешь ОДИН ответ модели ${it.model_short} на кейс ${it.case_id}. Презумпция незачёта: pass=true только если доказал, что ответ выполняет задачу без критических дефектов. Но и не выдумывай дефекты: незачёт тоже требует конкретного доказательства (цитата, пересчёт, отсутствующий файл).
${artefacts(it)}
${FACTORS}
Правила: сабстринг-ключи из голдена — подсказка, НЕ истина: синонимичная формулировка того же факта = зачёт; неверный факт при совпавшем ключе = незачёт. Ответ обрублен на полуслове = незачёт. Краткий, но полный и верный ответ = зачёт, многословие не награждается.
Верни СТРОГО структурный вердикт (минимум слов, максимум конкретики).`

const refutePrompt = (it, v) => `Ты — оппонент судьи в бенчмарке Σ. Судья вынес вердикт по ответу ${it.model_short} на кейс ${it.case_id}:
${JSON.stringify(v)}
Твоя работа — СЛОМАТЬ этот вердикт:
- если pass=true → найди причину незачёта, которую судья пропустил (пересчитай числа python-ом, открой график Read-ом и посмотри, что на нём РЕАЛЬНО нарисовано, сверь утверждения с result тулзов в трейсе, поищи лик служебного текста/не-русский кусок/битую формулу);
- если pass=false → докажи, что это ложный незачёт (судья придрался к отсутствию слова при верной синонимичной формулировке; посчитал дефектом то, что задачей не требовалось; не заметил, что требование выполнено в другом месте ответа).
${artefacts(it)}
Проверь ФАКТИЧЕСКИ: не рассуждай о том, что можно проверить командой. Если после честной атаки вердикт устоял — agrees=true. Верни строгий JSON.`

const tiebreakPrompt = (it, v, r) => `Ты — арбитр бенчмарка Σ. По ответу ${it.model_short} на кейс ${it.case_id} судья и оппонент разошлись.
Вердикт судьи: ${JSON.stringify(v)}
Возражение оппонента: ${JSON.stringify(r)}
${artefacts(it)}
Перепроверь СПОРНЫЕ пункты сам (команды выше; пересчёт python-ом; графики открой Read-ом). Решение принимай по доказательствам, не по авторитету. Верни строгий структурный вердикт.`

phase('Judge')
log(`${ITEMS.length} ответов на въедливый суд`)

const results = await pipeline(
  ITEMS,
  (it) => agent(judgePrompt(it), {
    schema: VERDICT_SCHEMA, phase: 'Judge', label: `judge:${it.model_dir}/${it.case_id}`,
  }),
  (v, it) => v && agent(refutePrompt(it, v), {
    schema: REFUTE_SCHEMA, phase: 'Refute', label: `refute:${it.model_dir}/${it.case_id}`,
  }).then((r) => ({ v, r, it })),
  async (x) => {
    if (!x) return null
    const { v, r, it } = x
    if (!r || r.agrees) return { it, final: v, disputed: false }
    const t = await agent(tiebreakPrompt(it, v, r), {
      schema: VERDICT_SCHEMA, phase: 'Tiebreak', effort: 'high',
      label: `tiebreak:${it.model_dir}/${it.case_id}`,
    })
    return { it, final: t || v, disputed: true, refuter_said: r.corrected_pass }
  },
)

const done = results.filter(Boolean)
const verdicts = done.map(({ it, final, disputed }) => ({
  case_id: it.case_id,
  model_short: it.model_short,
  answer_sha1: it.answer_sha1,
  pass: final.pass,
  score: final.score,
  factors: final.factors,
  critical_defects: final.critical_defects,
  reason: (disputed ? '[спор решён арбитром] ' : '') + final.reason,
}))
log(`готово: ${verdicts.length}/${ITEMS.length}, зачтено ${verdicts.filter((v) => v.pass).length}, споров ${done.filter((d) => d.disputed).length}`)
return { verdicts }
