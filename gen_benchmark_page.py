#!/usr/bin/env python3
"""Render eval/bench/*/bench.json into a single static page at
/var/www/sigma/docs/benchmark/index.html (served at sigma.fmin.xyz/benchmark).

Honest by construction: every headline number is recomputed from the raw cases
via case_state() — a stored pass:true on a timed-out/oborvanный answer does NOT
count as a clean pass. Cost/token columns auto-hide until the data exists (no
fake $0 / 0-0). Cost shown is REAL OpenRouter spend (never estimated).
Regenerate any time — handles partial data (only some models done)."""
import json, html, sys, time, statistics
from pathlib import Path

ROOT = Path("/root/sigma_assistant")
EVAL = ROOT / "eval"
BENCH = sorted(EVAL.glob('bench_v*'), key=lambda p: (len(p.name), p.name))[-1]  # последняя версия; build_all рендерит все
OUT = Path("/var/www/sigma/docs/benchmark/index.html")
ASSISTANT_JS = Path("/var/www/sigma/docs/assistant/assistant.js")


def fmt_tool_result(raw, cap=6000):
    """Стена однострочного JSON с юникод-эскейпами нечитаема. Парсим и красиво
    разворачиваем (отступы, живая кириллица); не-JSON оставляем как есть."""
    try:
        pretty = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
    except Exception:
        pretty = raw
    if len(pretty) > cap:
        pretty = pretty[:cap] + "\n…[обрезано; полностью — в bench.json]"
    return pretty


def fmt_call_args(raw, cap=6000):
    """Аргументы вызова: {"code": "..."} → сам код, иначе pretty-JSON."""
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and len(obj) == 1 and isinstance(next(iter(obj.values())), str):
            pretty = next(iter(obj.values()))
        else:
            pretty = json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        pretty = raw
    if len(pretty) > cap:
        pretty = pretty[:cap] + "\n…[обрезано; полностью — в bench.json]"
    return pretty


def load_tool_icons():
    """Иконки/лейблы тулзов — ровно те же, что в виджете на сайте
    (TOOL_ICONS/TOOL_LABELS/DOT_ICON из assistant.js). Парсим объект-литерал;
    если структура уехала — страница просто падает на текстовый рендер."""
    import re as _re
    icons, labels, dot = {}, {}, ""
    pair = _re.compile(r"""(\w+):\s*\n?\s*(['"])((?:(?!\2).|\\.)*)\2""")
    try:
        t = ASSISTANT_JS.read_text(encoding="utf-8")
        def block(name):
            m = _re.search("const " + name + r" = \{(.*?)\n  \};", t, _re.S)
            return m.group(1) if m else ""
        for k, _q, v in pair.findall(block("TOOL_ICONS")):
            icons[k] = v
        for k, _q, v in pair.findall(block("TOOL_LABELS")):
            labels[k] = v
        m = _re.search(r"const DOT_ICON =\s*\n?\s*'((?:[^'\\]|\\.)*)'", t)
        dot = m.group(1) if m else ""
    except Exception as e:
        print(f"  ⚠ tool icons not loaded from assistant.js: {e}")
    return icons, labels, dot

CAT_LABEL = {
    "rag_basic": "Факты", "definition": "Определения", "structural": "Теоремы",
    "compute_pure": "Расчёт", "compute_plot": "Графики", "multi_hop": "Multihop",
    "vision_refine": "Vision-refine", "out_of_scope": "Отказ",
}
CAT_ORDER = ["rag_basic", "definition", "structural", "compute_pure",
             "compute_plot", "multi_hop", "vision_refine", "out_of_scope"]

# A run is "broken" — not a real measurement of the model — when it hits the
# wall-clock cap or returns an oborvanный огрызок. These never count as clean.
CAP_S = 480       # adaptive hard ceiling (eval/run_eval.py ASK_HARD_CEIL_S)
TIMEOUT_S = 171   # informational only: elapsed≥this = ran long (no longer = broken)
MIN_ANS = 40      # shorter than this = оборванный ответ, not a reply
DEAD_FRAC = 0.5   # >half the cases broken ⇒ infra-failed run, not a quality score

GLYPH = {"pass": ("✓", "ok"), "fail": ("✕", "no"), "broken": ("⚠", "warn"),
         "pending": ("⏳", "pend")}


def is_raw_toolcall(a):
    """A dumped, UNEXECUTED tool call left as the final answer. The agent loop
    consumes only NATIVE tool_calls; a model that emits a textual/fenced tool call
    in `content` dead-ends with the raw JSON/code shown as its 'answer'. That is a
    protocol DNF (the harness failed to obtain a real answer), NOT the model
    answering the question wrong — so it must count as broken, not fail."""
    s = (a or "").strip()
    if not s:
        return False
    low = s.lower()
    tm = ('"type"' in s or '"query"' in s) and (
        "search_textbook" in low or "read_chapter" in low
        or "find_definition" in low or "find_theorem" in low or '"type"' in s)
    if s.startswith("```json") and tm:
        return True
    if s[0] in "[{" and tm:
        return True
    if s.startswith("```python"):              # unexecuted code block as the whole answer
        parts = s.split("```")
        after = "```".join(parts[2:]).strip() if len(parts) > 2 else ""
        if len(after) < 80:
            return True
    return False


def esc(s):
    return html.escape(str(s if s is not None else ""))


def money(x, p=5):
    """Real cost or em-dash if not measured for this run (never a fake $0)."""
    return "—" if x is None else f"${x:.{p}f}"


def plural(n, forms):
    """Russian plural: forms = (one, few, many) → 'модель/модели/моделей'."""
    n = abs(int(n)) % 100
    if 11 <= n <= 14:
        return forms[2]
    d = n % 10
    if d == 1:
        return forms[0]
    if 2 <= d <= 4:
        return forms[1]
    return forms[2]


def case_state(c):
    """Honest per-case state, recomputed from raw data — not the stored pass flag.
    Returns (state, cause) where state ∈ {'pass','fail','broken'}.

    'broken' = the eval failed to OBTAIN an answer (DNF), NOT the model answering
    wrong. Driven by the `no_answer` flag (set by run_eval after retries) and the
    answer length — NOT by raw elapsed: the adaptive timeout lets a genuinely
    working agentic case run long and still PASS, so high elapsed alone no longer
    means broken (incident 2026-06-10)."""
    if c.get("protocol_dnf"):
        return "broken", "протокольный сбой (тулы недоступны)"
    if c.get("judge_pending"):
        # Judged категория без вердикта для ЭТОГО ответа: не «провал», а очередь
        # судьи. Никогда не публикуется как ✕ (аудит 2026-07-13, critical #2).
        return "pending", "ждёт судью"
    alen = len((c.get("answer") or "").strip())
    if c.get("no_answer") or alen == 0:
        return "broken", "нет ответа"
    # A short answer the grader PASSED is a valid terse reply ("c = 6", "Привет! Чем
    # могу помочь?"), NOT a truncated огрызок — don't punish brevity. Only short AND
    # failed answers are treated as broken, where the brevity likely means truncation.
    if alen < MIN_ANS and not c.get("pass"):
        return "broken", "оборван"
    if is_raw_toolcall(c.get("answer")):
        return "broken", "сырой tool-call (протокол не выполнен)"
    return ("pass", "") if c.get("pass") else ("fail", "промах по ключу")


def summarize(b):
    cases = b["cases"]
    states = [case_state(c)[0] for c in cases]
    n = len(cases) or 1
    broken = states.count("broken")
    pending = states.count("pending")
    # latency only over cases that actually completed — timeouts pin at the cap
    # and would otherwise make every model look equally slow.
    good_els = sorted((c.get("elapsed", 0) or 0)
                      for c, st in zip(cases, states) if st != "broken")
    return {
        "n": len(cases),
        "clean": states.count("pass"),
        "fail": states.count("fail"),
        "broken": broken,
        "pending": pending,   # unjudged: вне числителя И вне знаменателя
        "answered": len(cases) - broken,
        "dead": broken / n > DEAD_FRAC,   # infra-failed run, not a quality score
        "timeouts": sum(1 for c in cases if (c.get("elapsed", 0) or 0) >= TIMEOUT_S),
        "rate": states.count("pass") / max(1, n - pending),
        "median": statistics.median(good_els) if good_els else 0,
        "max": max(good_els) if good_els else 0,
    }


def cat_stats(b):
    out = {}
    for c in b["cases"]:
        st = case_state(c)[0]
        d = out.setdefault(c["category"], {"clean": 0, "total": 0, "pending": 0})
        if st == "pending":
            d["pending"] += 1   # не судимое не входит в знаменатель ячейки
            continue
        d["total"] += 1
        if st == "pass":
            d["clean"] += 1
    return out


def heat(rate):
    """clean-rate 0..1 → solid HSL red→amber→green (the rate pill)."""
    h = 120 * max(0.0, min(1.0, rate))
    return f"hsl({h:.0f} 62% 42%)"


def heat_bg(rate):
    """Heatmap cell bg: vary BOTH hue and lightness so 0/50/100% are clearly
    distinct (the old flat 93% lightness washed everything to one beige)."""
    rate = max(0.0, min(1.0, rate))
    h = 120 * rate
    l = 90 - 16 * (1 - rate)          # 90% (good) → 74% (bad)
    s = 55 + 18 * abs(rate - 0.5) * 2  # more saturated toward the extremes
    return f"hsl({h:.0f} {s:.0f}% {l:.0f}%)"


def short_model(m):
    return m.replace(":free", " ·free").split("/")[-1]


def scatter_svg(order, scoremap):
    """Inline SVG cost-vs-quality scatter (no JS/deps). x = стоимость ПРОГОНА
    (total_cost_usd, log), y = clean-rate. Decision is made per full run, not
    per question, so the axis is the whole-run cost. The Pareto frontier is
    drawn so dominated models (pay more, score less) visibly fall off it."""
    import math
    pts = []
    for b in order:                                   # order is already rate-sorted (best first)
        cq = b.get("total_cost_usd")
        if cq is None or cq <= 0:
            continue
        pts.append((cq, scoremap[b["model"]], short_model(b["model"])))
    if len(pts) < 2:
        return ""
    # number every model by leaderboard rank; the number is shown in the dot AND in the
    # legend, so ALL models are named without burying the plot in overlapping text.
    pts = [(c, r, name, i + 1) for i, (c, r, name) in enumerate(pts)]
    W, H, ml, mr, mt, mb = 720, 300, 54, 24, 16, 40
    pw, ph = W - ml - mr, H - mt - mb
    xs = [math.log10(p[0]) for p in pts]
    xmin, xmax = min(xs), max(xs)
    if xmax - xmin < 1e-9:
        xmin, xmax = xmin - 0.5, xmax + 0.5
    X = lambda c: ml + (math.log10(c) - xmin) / (xmax - xmin) * pw
    Y = lambda r: mt + (1 - r) * ph
    front = sorted((p for p in pts if not any(q[0] < p[0] and q[1] > p[1] for q in pts)),
                   key=lambda p: p[0])
    o = [f"<svg viewBox='0 0 {W} {H}' width='100%' style='max-width:{W}px;font:12px sans-serif;overflow:visible'>"]
    for r in (0, .25, .5, .75, 1):
        y = Y(r)
        o.append(f"<line x1='{ml}' y1='{y:.0f}' x2='{ml+pw}' y2='{y:.0f}' stroke='#f1f0ee'/>")
        o.append(f"<text x='{ml-8}' y='{y+4:.0f}' text-anchor='end' fill='#6b7280'>{r*100:.0f}%</text>")
    # vertical cost gridlines at nice 1/2/5×10^k values across the range, so you can read
    # roughly where each dot sits on cost (log scale).
    cmin, cmax = min(p[0] for p in pts), max(p[0] for p in pts)
    k = -4
    while 10 ** k <= cmax * 1.5:
        for mant in (1, 2, 5):
            v = mant * 10 ** k
            if cmin * 0.8 <= v <= cmax * 1.2:
                xv = X(v)
                o.append(f"<line x1='{xv:.0f}' y1='{mt}' x2='{xv:.0f}' y2='{mt+ph}' stroke='#f4f3f1'/>")
                lbl = f"${v:.2f}" if v >= 0.1 else f"${v:.3f}"
                o.append(f"<text x='{xv:.0f}' y='{mt+ph+16:.0f}' text-anchor='middle' fill='#9ca3af' font-size='11'>{lbl}</text>")
        k += 1
    o.append(f"<line x1='{ml}' y1='{mt+ph}' x2='{ml+pw}' y2='{mt+ph}' stroke='#e7e5e4'/>")
    if len(front) > 1:
        pl = " ".join(f"{X(c):.0f},{Y(r):.0f}" for c, r, *_ in front)
        o.append(f"<polyline points='{pl}' fill='none' stroke='#0284c7' stroke-width='1.5' stroke-dasharray='4 3' opacity='0.7'></polyline>")
    fset = {(c, r) for c, r, *_ in front}
    # Every model is a NUMBERED dot (frontier blue, dominated gray); the number ties it to
    # the legend below — all models named, zero overlapping text on the plot. Draw gray
    # first so blue frontier dots sit on top.
    for c, r, name, num in sorted(pts, key=lambda p: (p[0], p[1]) in fset):
        x, y = X(c), Y(r)
        onf = (c, r) in fset
        o.append(f"<circle cx='{x:.0f}' cy='{y:.0f}' r='9' fill='{'#0284c7' if onf else '#aab4c2'}' stroke='#fff' stroke-width='1.5'></circle>")
        o.append(f"<text x='{x:.0f}' y='{y+3.5:.0f}' text-anchor='middle' fill='#fff' font-size='10.5' font-weight='700'>{num}</text>")
    o.append(f"<text x='{ml+pw/2:.0f}' y='{H-3}' text-anchor='middle' fill='#6b7280'>стоимость прогона, $ (лог. шкала) →</text>")
    o.append("</svg>")
    # numbered legend — column-major: fill the left column top-to-bottom first, then the
    # middle, then the right (grid-auto-flow:column + fixed row count). Equal columns keep
    # it aligned regardless of name length.
    import math as _m
    rows_n = _m.ceil(len(pts) / 3)
    leg = [f"<div style='display:grid;grid-auto-flow:column;grid-template-rows:repeat({rows_n},auto);"
           f"grid-auto-columns:1fr;gap:5px 24px;margin-top:12px;font-size:12.5px'>"]
    for c, r, name, num in pts:
        onf = (c, r) in fset
        col = "#0284c7" if onf else "#64748b"
        leg.append(
            f"<span style='display:flex;align-items:center;gap:7px'>"
            f"<b style='display:inline-flex;width:18px;height:18px;border-radius:50%;background:{col};"
            f"color:#fff;font-size:10.5px;align-items:center;justify-content:center;flex:none'>{num}</b>"
            f"<span style='color:#1a1a1a'>{esc(name)}</span>"
            f"<span style='color:#94a3b8'>· {r*100:.0f}% · ${c:.3f}</span></span>")
    leg.append("</div>")
    return "".join(o) + "".join(leg)


def slugify(m):
    import re
    return re.sub(r"[^a-z0-9]+", "_", m.lower()).strip("_")


def load(bench_dir=None):
    benches = []
    for p in sorted((bench_dir or BENCH).glob("*/bench.json")):
        try:
            b = json.loads(p.read_text(encoding="utf-8"))
            b["_dir"] = p.parent.name
            benches.append(b)
        except Exception:
            pass
    return benches


CSS = """
:root{--fg:#1a1a1a;--mut:#6b7280;--line:#e7e5e4;--card:#faf9f7;--accent:#0284c7;
--ok:#15803d;--no:#b91c1c;--warn:#b45309}
*{box-sizing:border-box}
body{font:15px/1.55 -apple-system,BlinkMacSystemFont,'Nunito Sans',system-ui,sans-serif;color:var(--fg);margin:0;background:#fff}
.wrap{max-width:1100px;margin:0 auto;padding:46px 24px 96px}
h1{font-weight:800;letter-spacing:-.025em;margin:0 0 14px;font-size:30px}
h2{font-weight:700;margin:48px 0 6px;font-size:20px;letter-spacing:-.01em}
.cap{color:var(--mut);font-size:13px;margin:0 0 14px}
.sub{color:var(--mut);margin:0 0 6px}
/* lede: one sentence + the one number that matters */
.lede{font-size:21px;line-height:1.5;font-weight:500;margin:0 0 6px;max-width:760px}
.lede b{font-weight:800}
.hero{font-variant-numeric:tabular-nums}
/* headline stat band */
.stats{display:flex;flex-wrap:wrap;gap:12px;margin:20px 0 4px;font-variant-numeric:tabular-nums}
.stat{flex:1;min-width:148px;border:1px solid var(--line);border-radius:16px;padding:15px 17px;background:linear-gradient(180deg,#fff,var(--card))}
.stat .n{font-size:27px;font-weight:800;letter-spacing:-.02em;line-height:1.08}
.stat .n small{font-size:15px;font-weight:700;color:var(--mut)}
.stat .k{font-size:12.5px;color:var(--mut);margin-top:4px}
.stat.hi{border-color:#cfe9f7;background:linear-gradient(180deg,#f6fbff,#eef7fd)}
.stat.hi .n{color:var(--accent)}
tr.lead td{background:linear-gradient(180deg,#f6fbff,#eef7fd)!important}
th.rk,td.rk{width:34px;text-align:center;color:var(--mut);font-variant-numeric:tabular-nums;font-weight:700}
.tags{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:14px 0 2px}
.tag{font-size:12.5px;color:var(--mut);border:1px solid var(--line);border-radius:999px;padding:3px 11px;background:var(--card)}
.tag b{color:var(--fg);font-weight:700}
.tag.warn{color:var(--warn);border-color:#fde6c8;background:#fff9f0}
.tag.prov{color:var(--accent);border-color:#cfe9f7;background:#f0f9ff}
.note{color:var(--mut);font-size:13px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 15px;margin:16px 0}
.note b{color:var(--fg)}
.note code{background:#eef0f2;border-radius:4px;padding:1px 5px;font-size:12px}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:9px 12px;text-align:center;border-bottom:1px solid var(--line)}
th{font-weight:600;color:var(--mut);font-size:11.5px;text-transform:uppercase;letter-spacing:.04em}
td.l,th.l{text-align:left}
tbody tr:hover{background:#fafafa}
.model{font-weight:600}
.rate{font-weight:800;font-size:15px;border-radius:7px;padding:3px 9px;color:var(--fg);display:inline-block;min-width:50px}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.cost{font-variant-numeric:tabular-nums}
.muted{color:var(--mut)}
.pill{font-size:11px;color:var(--mut);border:1px solid var(--line);border-radius:999px;padding:1px 7px;display:inline-block;margin-top:2px}
/* failure section — the page's real job */
.fails{margin:6px 0 0;border:1px solid var(--line);border-radius:12px;overflow:hidden}
.fr{display:flex;gap:12px;align-items:flex-start;padding:11px 15px;border-bottom:1px solid var(--line);cursor:pointer}
.fr:last-child{border-bottom:none}
.fr:hover{background:#fafafa}
.fr .g{font-size:15px;font-weight:800;line-height:1.4;flex:none;width:18px;text-align:center}
.fr .body{flex:1;min-width:0}
.fr .q{font-size:14px}
.fr .meta{font-size:11.5px;color:var(--mut);margin-top:2px}
.cause{font-size:11px;border-radius:999px;padding:1px 8px;font-weight:600;white-space:nowrap}
.cause.broken{color:var(--warn);background:#fff4e6}
.cause.fail{color:var(--no);background:#fdecec}
/* per-question matrix */
.qrow td.l{max-width:560px}
.qcat{font-size:10.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
.cellmark{font-weight:800;cursor:pointer;border-radius:6px;padding:2px 9px;display:inline-block;min-width:26px;font-size:15px}
.cellmark:hover{background:#f1f5f9}
.ok{color:var(--ok)}.no{color:var(--no)}.warn{color:var(--warn)}.pend{color:#94a3b8}
.judgebox{border:1px solid #e2e8f0;border-radius:10px;padding:10px 14px;margin:12px 0;font-size:13px;line-height:1.5}
.judgebox.jok{border-color:#bfe6c8;background:#f3fbf5}
.judgebox.jno{border-color:#f2c4c4;background:#fdf4f4}
.judgebox .jhead{font-weight:700;margin-bottom:4px}
.judgebox.jok .jhead{color:var(--ok)}.judgebox.jno .jhead{color:var(--no)}
.judgebox .jreason{color:#334155}
.judgebox .jcrit{margin-top:6px;color:#7f1d1d}
.judgebox .jcrit ul{margin:4px 0 0 18px;padding:0}
.heatcell{border-radius:6px;font-weight:700;color:#0a0a0a;font-variant-numeric:tabular-nums}
.heatcell.lown{opacity:.5}
.heatcell .nn{font-size:10px;color:#475569;display:block;font-weight:600}
.legend{display:flex;gap:16px;align-items:center;font-size:12px;color:var(--mut);margin:8px 0 0}
.legend i{width:15px;height:15px;border-radius:4px;display:inline-block;vertical-align:-3px;margin-right:5px}
.foot{color:var(--mut);font-size:12px;margin-top:54px;border-top:1px solid var(--line);padding-top:14px}
a{color:var(--accent)}
/* detail drawer + scrim */
#scrim{position:fixed;inset:0;background:rgba(15,23,42,.32);opacity:0;pointer-events:none;transition:opacity .2s;z-index:40}
#scrim.open{opacity:1;pointer-events:auto}
#detail{position:fixed;inset:auto 0 0 0;max-height:62vh;overflow:auto;background:#fff;border-top:2px solid var(--accent);box-shadow:0 -10px 34px rgba(0,0,0,.16);padding:20px 24px 26px;transform:translateY(110%);transition:transform .22s;z-index:50}
#detail.open{transform:none}
#detail h3{margin:0 0 5px;font-size:17px}
#detail .meta{color:var(--mut);font-size:13px;margin-bottom:11px;line-height:1.5}
#detail .ans{word-break:break-word;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px 15px;font-size:13.5px;line-height:1.55;max-width:820px}
#detail .ans p{margin:0 0 9px}#detail .ans p:last-child{margin-bottom:0}
#detail .ans h3,#detail .ans h4,#detail .ans h5,#detail .ans h6{margin:12px 0 5px;font-size:14.5px;font-weight:700}
#detail .ans ul,#detail .ans ol{margin:6px 0 9px;padding-left:22px}#detail .ans li{margin:2px 0}
#detail .ans pre.sigma-code{background:#0f172a;color:#e2e8f0;border-radius:8px;padding:10px 12px;overflow-x:auto;font-size:12.5px;line-height:1.45;margin:8px 0}
#detail .ans pre.sigma-code code{background:none;padding:0;color:inherit;font-size:inherit}
#detail .ans code{background:#eef0f2;border-radius:4px;padding:1px 5px;font-size:12.5px}
#detail .ans blockquote{margin:8px 0;padding:2px 12px;border-left:3px solid var(--line);color:var(--mut)}
#detail .ans table.sigma-tbl{border-collapse:collapse;width:auto;font-size:13px;margin:8px 0}
#detail .ans table.sigma-tbl th,#detail .ans table.sigma-tbl td{border:1px solid var(--line);padding:4px 9px;text-align:left}
#detail .ans table.sigma-tbl th{background:var(--card);font-weight:700}
#detail .ans .katex-error{color:#b91c1c}
#detail .ans hr{border:none;border-top:1px solid var(--line);margin:10px 0}
#detail .ans .figs{margin-top:12px;display:flex;flex-direction:column;gap:8px}
#detail .ans .figs .figcap{font-size:12px;color:var(--mut);font-weight:600}
#detail .ans .figs img.fig{max-width:100%;border:1px solid var(--line);border-radius:8px;background:#fff}
#detail .toolchips{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 10px}
#detail .toolchip{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600;color:var(--mut);border:1px solid var(--line);border-radius:999px;padding:3px 10px 3px 7px;background:#fff}
#detail .toolchip svg{width:14px;height:14px;flex:none}
#detail .toolchip .arr{color:var(--line);font-weight:400}
#detail .toolchips.clickable .toolchip{cursor:pointer;transition:border-color .15s,color .15s,box-shadow .15s}
#detail .toolchips.clickable .toolchip:hover{border-color:var(--accent);color:var(--accent);box-shadow:0 1px 4px rgba(2,132,199,.15)}
#detail .toolchips .thint{font-size:11.5px;color:var(--mut);align-self:center;margin-left:2px;user-select:none}
#detail .tracelist{display:grid;grid-template-rows:0fr;opacity:0;margin:0 0 10px;border:1px solid var(--line);border-radius:12px;background:var(--card);transition:grid-template-rows .22s ease,opacity .22s ease}
#detail .tracelist.open{grid-template-rows:1fr;opacity:1}
#detail .tracelist>div{overflow:hidden}
#detail .tracelist .tr{display:flex;align-items:baseline;gap:9px;padding:7px 12px;font-size:13px;border-top:1px solid var(--line)}
#detail .tracelist .tr:first-child{border-top:0}
#detail .tracelist .tr:nth-child(even){background:#fff}
#detail .tracelist .tr .st{font-size:11px;color:var(--mut);min-width:16px;text-align:right;font-variant-numeric:tabular-nums}
#detail .tracelist .tr svg{width:13px;height:13px;flex:none;align-self:center;color:var(--accent)}
#detail .tracelist .tr .tn{font-weight:600;white-space:nowrap}
#detail .tracelist .tr .ta{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:var(--fg);opacity:.75;word-break:break-word;flex:1}
#detail .tracelist .tr .ts{font-size:11px;color:var(--mut);white-space:nowrap;border:1px solid var(--line);border-radius:999px;padding:1px 8px;background:#fff}
#detail .tracelist .tr.hl{background:#eef7ff;box-shadow:inset 3px 0 0 var(--accent)}
#detail .tracelist .tr.expandable{cursor:pointer}
#detail .tracelist .tres{display:none;margin:0;padding:8px 12px 10px 37px;border-top:1px dashed var(--line);background:#fff}
#detail .tracelist .tres.open{display:block}
#detail .tracelist .tres pre{margin:0;font-size:11.5px;line-height:1.5;white-space:pre-wrap;word-break:break-word;max-height:300px;overflow:auto;color:var(--fg);opacity:.85}
#detail .ans figure.figref{margin:8px 0;display:flex;flex-direction:column;gap:4px}
#detail .ans figure.figref img{max-width:100%;border:1px solid var(--line);border-radius:8px;background:#fff}
#detail .ans .figcap{font-size:12px;color:var(--mut);line-height:1.4}
#detail .x{position:absolute;top:10px;right:14px;cursor:pointer;width:40px;height:40px;border-radius:10px;font-size:22px;color:var(--mut);border:none;background:none;line-height:1}
#detail .x:hover{background:#f1f5f9}
@media(min-width:920px){
  #detail{inset:0 0 0 auto;width:min(560px,44vw);max-height:100vh;border-top:none;border-left:2px solid var(--accent);transform:translateX(110%);box-shadow:-10px 0 34px rgba(0,0,0,.14)}
  #detail.open{transform:none}
}
/* this is a dashboard, not a textbook page — suppress the injected chat assistant */
.sigma-sheet,.sigma-launcher,.sigma-fragment-pill,.asst-drawer{display:none!important}
"""

# Ported VERBATIM from assistant.js renderMarkdown — so a benchmarked answer
# renders on this page EXACTLY as a reader sees it on the site: tables, lists,
# bold/italic, code blocks, blockquotes, line breaks AND inline KaTeX. The old
# page did `textContent` + KaTeX-only, so every table/list/`**bold**` showed as
# raw markdown text. Raw string (r'''…''') keeps all regex backslashes intact.
RENDER_JS = r'''
function escapeHtml(s){
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
function renderKatex(expr,displayMode){
  if(!window.katex) return escapeHtml((displayMode?"$$":"$")+expr+(displayMode?"$$":"$"));
  try{
    return window.katex.renderToString(expr,{displayMode,throwOnError:false,output:"html",strict:"ignore",trust:false});
  }catch(_){ return "<code>"+escapeHtml(expr)+"</code>"; }
}
function renderMarkdown(md){
  const stash=[];
  // Sentinel must survive .trim() (table cells) and trailing-space strip (block
  // pass) and HTML-escape — the old " S<n> " token lost its spaces there, so the
  // restore regex missed it and display/$-in-table formulas rendered as raw "S0".
  const STASH=(item)=>{ stash.push(item); return ""+(stash.length-1)+""; };
  let s=String(md);
  s=s.replace(/\$\$([\s\S]+?)\$\$/g,(_,e)=>STASH({k:"mblock",v:e}));
  s=s.replace(/(?<!\$|\\)\$([^\n$]+?)\$(?!\$)/g,(_,e)=>STASH({k:"minline",v:e}));
  s=s.replace(/```(\w+)?\n([\s\S]*?)```/g,(_,lang,code)=>STASH({k:"code-block",lang,code}));
  s=s.replace(/`([^`\n]+)`/g,(_,code)=>STASH({k:"code-inline",code}));
  s=escapeHtml(s);
  s=s.replace(/(^\|.+\|\n\|[-:|\s]+\|\n(?:^\|.+\|\n?)+)/gm,(block)=>{
    const lines=block.trim().split("\n");
    const head=lines[0].split("|").slice(1,-1).map(c=>c.trim());
    const body=lines.slice(2).map(l=>l.split("|").slice(1,-1).map(c=>c.trim()));
    return "<table class='sigma-tbl'><thead><tr>"+head.map(h=>"<th>"+h+"</th>").join("")+
      "</tr></thead><tbody>"+body.map(r=>"<tr>"+r.map(c=>"<td>"+c+"</td>").join("")+"</tr>").join("")+
      "</tbody></table>";
  });
  const lines=s.split("\n");
  const out=[];
  let listKind=null;
  const closeList=()=>{ if(listKind){ out.push("</"+listKind+">"); listKind=null; } };
  for(const raw of lines){
    const line=raw.replace(/\s+$/,"");
    let m;
    if((m=/^(#{1,6})\s+(.+)$/.exec(line))){ closeList(); const lvl=Math.min(6,m[1].length+2); out.push("<h"+lvl+">"+m[2]+"</h"+lvl+">"); }
    else if(/^[-*_]{3,}\s*$/.test(line)){ closeList(); out.push("<hr>"); }
    else if((m=/^\s*[-*]\s+(.+)$/.exec(line))){ if(listKind!=="ul"){ closeList(); out.push("<ul>"); listKind="ul"; } out.push("<li>"+m[1]+"</li>"); }
    else if((m=/^\s*\d+\.\s+(.+)$/.exec(line))){ if(listKind!=="ol"){ closeList(); out.push("<ol>"); listKind="ol"; } out.push("<li>"+m[1]+"</li>"); }
    else if((m=/^&gt;\s*(.+)$/.exec(line))){ closeList(); out.push("<blockquote>"+m[1]+"</blockquote>"); }
    else if(!line.trim()){ closeList(); out.push(""); }
    else { closeList(); out.push(line); }
  }
  closeList();
  s=out.join("\n");
  // Images first: real ones (book figures /figures/…, http(s), data:) render inline;
  // the model's invented placeholder refs (png://, media://, sandbox:, attachment:, bare
  // names) become a caption, not a broken "!"+link.
  s=s.replace(/!\[([^\]\n]*)\]\(([^()\s]+)\)/g,(_,alt,u)=>{
    if(/^(https?:\/\/|data:image\/|\/)/.test(u)){
      const cap=alt?'<figcaption class="figcap">'+alt+'</figcaption>':'';
      return '<figure class="figref"><img src="'+u+'" loading="lazy">'+cap+'</figure>';
    }
    return alt?'<span class="figcap">🖼 '+alt+'</span>':'';
  });
  s=s.replace(/\[([^\]\n]+)\]\(([^()\s]+)\)/g,(_,t,u)=>{
    const external=/^https?:\/\//.test(u)&&!/sigma\.fmin\.xyz/.test(u);
    const tgt=external?' target="_blank" rel="noopener"':'';
    return '<a href="'+u+'" class="sigma-link"'+tgt+'>'+t+'</a>';
  });
  s=s.replace(/\*\*([^*\n]+)\*\*/g,"<strong>$1</strong>");
  s=s.replace(/(?<!\w)\*([^*\n]+)\*(?!\w)/g,"<em>$1</em>");
  const BLOCK=/^<(h[1-6]|ul|ol|li|hr|blockquote|pre|table|tr|td|th|thead|tbody|p|div|figure|figcaption)/i;
  const BLOCK_END=/^<\/(h[1-6]|ul|ol|li|hr|blockquote|pre|table|tr|td|th|thead|tbody|p|div|figure|figcaption)/i;
  const finalLines=s.split("\n");
  const result=[];
  let para=[];
  const flushPara=()=>{ if(para.length){ result.push("<p>"+para.join("<br>")+"</p>"); para=[]; } };
  for(const ln of finalLines){
    const t=ln.trim();
    if(!t){ flushPara(); continue; }
    if(BLOCK.test(t)||BLOCK_END.test(t)){ flushPara(); result.push(ln); }
    else para.push(ln);
  }
  flushPara();
  s=result.join("\n");
  s=s.replace(/(\d+)/g,(_,i)=>{
    const it=stash[Number(i)];
    if(it.k==="mblock") return renderKatex(it.v,true);
    if(it.k==="minline") return renderKatex(it.v,false);
    if(it.k==="code-block"){ const code=escapeHtml(it.code); return "<pre class='sigma-code"+(it.lang?" lang-"+it.lang:"")+"'><code>"+code+"</code></pre>"; }
    if(it.k==="code-inline") return "<code>"+escapeHtml(it.code)+"</code>";
    return "";
  });
  return s;
}
'''

DRAWER_JS = """
let D={};
fetch('data.json?b=__DATAVER__').then(r=>r.json()).then(j=>{D=j;document.body.classList.add('data-ready');})
  .catch(e=>console.error('data.json load failed',e));
const TOOL_ICONS=__ICONS__, TOOL_LABELS=__LABELS__, DOT_ICON=__DOT__;
function esc0(x){const d=document.createElement('span');d.textContent=x;return d.innerHTML;}
const detail=document.getElementById('detail'),scrim=document.getElementById('scrim');
let lastEl=null;
function openDetail(el){
  const d=D[el.dataset.k]; if(!d) return;
  lastEl=el;
  const g=d.state==='pass'?'✓':d.state==='fail'?'✕':d.state==='pending'?'⏳':'⚠';
  document.getElementById('dt').textContent=g+' '+d.model+' · '+d.cat;
  let bits=['картинок: '+d.img,d.elapsed.toFixed(0)+' с'];
  const tc=document.getElementById('dtools'); tc.innerHTML=''; tc.classList.remove('clickable');
  const tl=document.getElementById('dtrace'); tl.innerHTML=''; tl.classList.remove('open');
  const hasTrace=d.trace&&d.trace.length;
  if(d.tools&&d.tools.length){
    d.tools.forEach((n,i)=>{
      if(i>0){const a=document.createElement('span');a.className='arr';a.textContent='→';tc.appendChild(a);}
      const sp=document.createElement('span'); sp.className='toolchip';
      sp.innerHTML=(TOOL_ICONS[n]||DOT_ICON)+'<span>'+(TOOL_LABELS[n]||n)+'</span>';
      if(hasTrace){sp.title='история вызовов';}
      tc.appendChild(sp);
    });
    if(hasTrace){
      tc.classList.add('clickable');
      const hint=document.createElement('span'); hint.className='thint'; hint.textContent='история ▾';
      hint.style.cursor='pointer';
      const setHint=()=>{hint.textContent=tl.classList.contains('open')?'история ▴':'история ▾';};
      hint.onclick=()=>{tl.classList.toggle('open'); setHint();};
      tc.appendChild(hint);
      const inner=document.createElement('div');
      const rows=[];
      d.trace.forEach((t,i)=>{
        const row=document.createElement('div'); row.className='tr';
        const args=t.args?esc0(t.args):'—', st=t.status?esc0(t.status):'';
        row.innerHTML='<span class=st>'+(i+1)+'</span>'
          +(TOOL_ICONS[t.tool]||DOT_ICON)
          +'<span class=tn>'+esc0(TOOL_LABELS[t.tool]||t.tool)+'</span>'
          +'<span class=ta>'+args+'</span>'
          +(st?'<span class=ts>'+st+'</span>':'');
        inner.appendChild(row); rows.push(row);
        if(t.result||t.call_args){
          row.classList.add('expandable'); row.title='показать вызов и ответ инструмента';
          const res=document.createElement('div'); res.className='tres';
          if(t.call_args){
            const c1=document.createElement('div'); c1.className='figcap'; c1.textContent='Вызов:';
            const p1=document.createElement('pre'); p1.textContent=t.call_args;
            res.appendChild(c1); res.appendChild(p1);
          }
          if(t.result){
            const c2=document.createElement('div'); c2.className='figcap'; c2.style.marginTop='8px'; c2.textContent='Ответ:';
            const p2=document.createElement('pre'); p2.textContent=t.result;
            res.appendChild(c2); res.appendChild(p2);
          }
          inner.appendChild(res);
          row.addEventListener('click',()=>res.classList.toggle('open'));
        }
      });
      tl.appendChild(inner);
      // чип = конкретный вызов: раскрыть панель и подсветить именно его
      tc.querySelectorAll('.toolchip').forEach((c,i)=>{
        c.onclick=()=>{
          const row=rows[i]; if(!row) return;
          const was=row.classList.contains('hl');
          rows.forEach(r=>r.classList.remove('hl'));
          if(tl.classList.contains('open')&&was){tl.classList.remove('open');}
          else{tl.classList.add('open'); row.classList.add('hl');
               setTimeout(()=>row.scrollIntoView({block:'nearest',behavior:'smooth'}),240);}
          setHint();
        };
      });
    }
  } else {
    tc.innerHTML='<span class="toolchip">'+DOT_ICON+'<span>без инструментов</span></span>';
  }
  if(d.cost!=null) bits.push('$'+d.cost.toFixed(5));
  if(d.cause) bits.push('состояние: '+d.cause);
  if(d.missing&&d.missing.length) bits.push('не хватило: '+d.missing.join(', '));
  // Вердикт LLM-судьи (семантические кейсы): зачёт/незачёт, score, причина,
  // критические дефекты. Детерминированные кейсы судятся кодом — блока нет.
  const dj=document.getElementById('dj'); dj.innerHTML='';
  if(d.judge_pass!=null||d.judge_score!=null){
    const jb=document.createElement('div');
    jb.className='judgebox '+(d.judge_pass?'jok':'jno');
    let h='<div class=jhead>'+(d.judge_pass?'✓ Зачёт':'✕ Незачёт')+' — вердикт судьи'
      +(d.judge_score!=null?' · score '+Math.round(d.judge_score*100)+'%':'')+'</div>';
    if(d.judge_reason) h+='<div class=jreason>'+esc0(d.judge_reason)+'</div>';
    if(d.judge_critical&&d.judge_critical.length)
      h+='<div class=jcrit><b>Критические дефекты ('+d.judge_critical.length+'):</b><ul>'
        +d.judge_critical.map(x=>'<li>'+esc0(x)+'</li>').join('')+'</ul></div>';
    jb.innerHTML=h; dj.appendChild(jb);
  }
  const da=document.getElementById('da');
  // Render the FULL markdown (tables, lists, bold, code, line breaks + inline
  // KaTeX) exactly as a reader sees it on the site — same renderMarkdown as
  // assistant.js. Broken formulas → .katex-error (red); count & flag them, as a
  // malformed formula is a real model/agent defect worth surfacing.
  da.innerHTML = d.answer ? renderMarkdown(d.answer) : '(пустой ответ)';
  // Agent-drawn figures (python/matplotlib → .sigma-figure), saved as base64
  // with the run. Render them under the answer so a vision question's graph is
  // actually visible in the drawer (not just counted).
  if(d.figures && d.figures.length){
    const fw=document.createElement('div'); fw.className='figs';
    const cap=document.createElement('div'); cap.className='figcap';
    cap.textContent='Графики, построенные агентом ('+d.figures.length+'):';
    fw.appendChild(cap);
    for(const src of d.figures){ const im=document.createElement('img'); im.className='fig'; im.src=src; im.loading='lazy'; fw.appendChild(im); }
    da.appendChild(fw);
  }
  if(d.img>0 && (!d.figures || !d.figures.length)){
    const nb=document.createElement('div'); nb.className='figcap';
    nb.textContent='⚠ Агент построил картинок: '+d.img+', но раннер этого прогона их не сохранил — графики видны на скрине ниже.';
    da.appendChild(nb);
  }
  if(d.shot){
    const sw=document.createElement('div'); sw.className='figs';
    const cap=document.createElement('div'); cap.className='figcap';
    cap.textContent='Скрин ответа на живой странице (клик — открыть в полном размере):';
    sw.appendChild(cap);
    const a=document.createElement('a'); a.href=d.shot; a.target='_blank'; a.rel='noopener';
    const im=document.createElement('img'); im.className='fig'; im.src=d.shot; im.loading='lazy';
    a.appendChild(im); sw.appendChild(a);
    da.appendChild(sw);
  }
  const broken = da.querySelectorAll('.katex-error').length;
  if(broken) bits.push('⚠ битых формул: '+broken);
  document.getElementById('dm').textContent='Вопрос: '+d.q+'  ·  '+bits.join('  ·  ');
  detail.classList.add('open'); scrim.classList.add('open');
  if(window.innerWidth<920) el.scrollIntoView({block:'center',behavior:'smooth'});
}
function closeDetail(){detail.classList.remove('open');scrim.classList.remove('open');}
document.querySelectorAll('[data-k]').forEach(el=>el.addEventListener('click',()=>openDetail(el)));
scrim.addEventListener('click',closeDetail);
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDetail();});
"""


def build(bench_dir=None, out=None, version=None, versions=()):
    global BENCH, OUT
    BENCH = bench_dir or BENCH
    OUT = out or OUT
    benches = load(BENCH)
    if not benches:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(
            "<!doctype html><html lang=ru><head><meta charset=utf-8>"
            "<meta name=viewport content=\"width=device-width,initial-scale=1\">"
            "<title>Σ Sigma — Бенчмарк агента</title>"
            "<style>body{font:16px/1.6 -apple-system,BlinkMacSystemFont,system-ui,sans-serif;"
            "color:#1a1a1a;margin:0;display:grid;place-items:center;min-height:100vh}"
            ".box{text-align:center;padding:24px}h1{font-weight:800;letter-spacing:-.02em}"
            ".sub{color:#6b7280}a{color:#0284c7}</style></head><body><div class=box>"
            "<h1>Σ Бенчмарк агента</h1>"
            "<p class=sub>Бенчмарк ещё не запускался.</p>"
            "<p class=sub><a href=\"/\">← к учебнику</a></p>"
            "</div></body></html>",
            encoding="utf-8")
        return

    # union of questions across models (id → {category, question})
    qmeta, qorder = {}, []
    for b in benches:
        for c in b["cases"]:
            if c["id"] not in qmeta:
                qmeta[c["id"]] = {"category": c["category"], "question": c["question"]}
                qorder.append(c["id"])
    qorder.sort(key=lambda i: (CAT_ORDER.index(qmeta[i]["category"]) if qmeta[i]["category"] in CAT_ORDER else 99, i))
    cats = [c for c in CAT_ORDER if any(qmeta[i]["category"] == c for i in qorder)]
    cell = {b["model"]: {c["id"]: c for c in b["cases"]} for b in benches}

    # recompute everything honestly from cases
    summ = {b["model"]: summarize(b) for b in benches}
    catst = {b["model"]: cat_stats(b) for b in benches}
    # split off infra-failed runs (free models that timed out / returned nothing):
    # they are NOT quality scores and must not sit in the ranking, heatmap, or lede.
    live = [b for b in benches if not summ[b["model"]]["dead"]]
    dead = [b for b in benches if summ[b["model"]]["dead"]]
    if not live:                       # degenerate: everything failed
        live, dead = benches, []

    # THE score: one blended continuous number over ALL cases — semantic cases get a
    # per-criterion rubric score (0..1), compute/plot cases the deterministic 0/1
    # (grade_rubric.py). This is the headline metric, the ranking key, AND the y-axis
    # of the cost↔quality plot. Falls back to binary clean-rate only if a run has no
    # rubric score yet.
    def score_of(b):
        # ONE score over the WHOLE question set: each answer earns weighted rubric points
        # (semantic cases by criteria met/partial/none; compute cases 0/1), summed and
        # divided by ALL questions. A model that just won't answer under a correct run
        # scores 0 on that question (not excluded) — the eval retries to get an answer;
        # if it still can't, 0 stands. Denominator is always the full set.
        cases = b["cases"]
        tot, denom = 0.0, 0
        for c in cases:
            st = case_state(c)[0]
            if st == "pending":
                continue                        # не судимое — вне score целиком
            denom += 1
            if st == "broken":
                tot += 0.0                      # no usable answer → 0 for that question
                continue
            # КУМУЛЯТИВНЫЙ score: смысловые кейсы несут судейский скор 0..1
            # (частичный кредит — 0.94 и 0.15 различимы), расчётные — 0/1 от
            # код-грейдера. Раньше всё падало в бинарный pass, и лидер с
            # частичными зачётами показывал ложные 100%.
            rs = c.get("judge_score")
            if rs is None:
                rs = c.get("rubric_score")
            tot += rs if rs is not None else (1.0 if c.get("pass") else 0.0)
        return tot / (denom or 1)
    order = sorted(live, key=lambda b: -score_of(b))  # best score first
    multi = len(live) > 1

    has_cost = any(b.get("total_cost_usd") is not None for b in live)
    has_tok = any(sum(c.get("prompt_tokens", 0) or 0 for c in b["cases"]) for b in live)
    provisional = any(b.get("provisional") for b in live)

    P = []
    A = P.append
    A("<!doctype html><html lang=ru><head><meta charset=utf-8>")
    A("<meta name=viewport content=\"width=device-width,initial-scale=1\">")
    A("<title>Σ Sigma — Бенчмарк агента</title>")
    # KaTeX — same stack as the site, so answers render exactly as a reader sees them.
    # throwOnError:false → broken LaTeX renders red (.katex-error), which we then count:
    # a malformed formula is a model/agent defect and must be visible, not hidden.
    A("<link rel=stylesheet href=\"https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css\">")
    A("<script defer src=\"https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js\"></script>")
    A("<script defer src=\"https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js\"></script>")
    A(f"<style>{CSS}</style></head><body><div class=wrap>")
    A("<h1>Σ Бенчмарк агента</h1>")
    if version:
        vlinks = " · ".join(
            f"<b>{esc(v)}</b>" if v == version else f"<a href='{esc(href)}'>{esc(v)}</a>"
            for v, href in versions) or esc(version)
        A(f"<div class=cap style='margin:-6px 0 10px'>Версия бенча: {vlinks} · {len(load(BENCH)[0]['cases']) if False else ''}"
          f"кейсов в датасете: {sum(1 for l in (BENCH / 'cases.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()) if (BENCH / 'cases.jsonl').exists() else '—'}</div>")

    # ---- One compact context line, then straight to the data ----
    top = order[0]
    s0 = summ[top["model"]]
    if multi:
        # Crown by the continuous score — it breaks the 28/29 ties the binary count can't.
        top_score = score_of(top)
        lead = (f"Лучший <b>score</b> среди {len(live)} "
                f"{plural(len(live),('модели','моделей','моделей'))} — "
                f"<b>{esc(short_model(top['model']))}</b> ({top_score*100:.0f}%).")
    else:
        lead = f"<b>{esc(short_model(top['model']))}</b> — score {score_of(top)*100:.0f}%."
    prov = " · <b class=prov>предварительный прогон</b>" if provisional else ""
    A(f"<p class=sub>Тот же агент, что на сайте — меняется <b>только модель</b>. {lead} "
      f"{len(qorder)} {plural(len(qorder),('вопрос','вопроса','вопросов'))} · "
      f"<a href=\"#how\">как считаем</a>.{prov}</p>")


    # ---- Leaderboard table FIRST (the hero) ----
    lead_score = max((score_of(b) for b in order), default=-1)
    A(f"<h2 style='margin-top:24px'>Лидерборд <span class=muted style='font-weight:400;font-size:14px'>"
      f"— по непрерывному score (0–100&nbsp;%), лидер выделен</span></h2>")
    A("<div class=scroll><table><thead><tr>")
    A("<th class=rk>#</th><th class=l>Модель</th><th>Score</th><th>Чисто</th><th>Время (медиана / макс)</th>")
    if has_cost:
        A("<th>$ всего</th><th>$ / вопрос</th>")
    if has_tok:
        A("<th>Токены (in/out)</th>")
    A("</tr></thead><tbody>")
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}
    for i, b in enumerate(order, 1):
        m = b["model"]; s = summ[m]
        A(f"<tr class={'lead' if score_of(b) == lead_score else ''}>")
        A(f"<td class=rk>{medal.get(i, i)}</td>")
        A(f"<td class='l model'>{esc(short_model(m))}<div class=pill>{esc(m)}</div></td>")
        # THE score first (ranking metric): weighted rubric points over ALL questions.
        sc = score_of(b)
        A(f"<td><span class=rate style='background:{heat_bg(sc)}'><b>{sc*100:.0f}%</b></span></td>")
        pend_note = (f"<div class=muted style='font-size:11px'>⏳{s['pending']} ждёт судью</div>"
                     if s.get("pending") else "")
        A(f"<td><span class=rate style='background:{heat_bg(s['rate'])}'>{s['clean']}/{s['n'] - s.get('pending', 0)}</span>"
          f"<div class=muted style='font-size:12px'>{s['rate']*100:.0f}%</div>{pend_note}</td>")
        A(f"<td class=muted>{s['median']:.0f} с / {s['max']:.0f} с</td>")
        if has_cost:
            A(f"<td class=cost><b>{money(b.get('total_cost_usd'), 4)}</b></td>")
            A(f"<td class='cost muted'>{money(b.get('cost_per_q_usd'))}</td>")
        if has_tok:
            pin = sum(c.get("prompt_tokens", 0) or 0 for c in b["cases"])
            pout = sum(c.get("completion_tokens", 0) or 0 for c in b["cases"])
            A(f"<td class='muted cost'>{pin:,}/{pout:,}</td>")
        A("</tr>")
    A("</tbody></table></div>")
    A("<div class=note style='margin-top:10px'><b>Score</b> — один непрерывный показатель 0–100&nbsp;%, "
      "<b>кумулятивный</b>: смысловые кейсы несут судейский скор 0..1 (судья → адверсариальный "
      "оппонент → арбитр; частичный кредит — 0.94 и 0.15 различимы, <b>critical</b>-дефект роняет скор), "
      "расчётные и графики — детерминированный код-грейдинг (0/1). Сумма баллов делится на <b>все вопросы</b>. "
      "Если модель при корректном запуске так и не выдала ответ (после ретраев) — это <b>0</b> за этот "
      "вопрос, а не вычёркивание. По этому score ранжируем и строим график «score ↔ стоимость». "
      f"«Чисто» (бинарное из {len(qorder)}) оставлено для сравнения.</div>")

    # ---- Cost vs quality scatter (under the leaderboard) ----
    if multi and has_cost:
        svg = scatter_svg(order, {b["model"]: score_of(b) for b in order})
        if svg:
            A("<h2>Score ↔ стоимость <span class=muted style='font-weight:400;font-size:14px'>"
              "— ось Y = score (0–100&nbsp;%), ось X = стоимость прогона; выше и левее тем лучше; "
              "пунктир — граница Парето (синие точки), номера на точках = модели в легенде ниже</span></h2>")
            A(f"<div class=scroll>{svg}</div>")

    # Infra-failed runs (dead) are simply omitted — no "didn't qualify" note (noise).

    # ---- Category map (heatmap) — right under the summary ----
    A("<h2>Карта по категориям <span class=muted style='font-weight:400;font-size:14px'>— доля чисто пройденных</span></h2>")
    A("<div class=scroll><table><thead><tr><th class=l>Модель</th>")
    for c in cats:
        A(f"<th>{esc(CAT_LABEL.get(c,c))}</th>")
    A("</tr></thead><tbody>")
    for b in order:
        cs = catst[b["model"]]
        A(f"<tr><td class='l model'>{esc(short_model(b['model']))}</td>")
        for c in cats:
            cc = cs.get(c)
            if not cc:
                A("<td class=muted>—</td>"); continue
            if not cc["total"]:
                A("<td class='heatcell muted'>⏳</td>"); continue   # вся категория ждёт судью
            rate = cc["clean"] / cc["total"]
            lown = " lown" if cc["total"] < 3 else ""
            nn = f"<span class=nn>n={cc['total']}</span>" if cc["total"] < 3 else ""
            pend = f"<span class=nn>⏳{cc['pending']}</span>" if cc.get("pending") else ""
            A(f"<td class='heatcell{lown}' style='background:{heat_bg(rate)}'>{cc['clean']}/{cc['total']}{nn}{pend}</td>")
        A("</tr>")
    A("</tbody></table></div>")
    A("<div class=legend>"
      f"<span><i style='background:{heat_bg(0)}'></i>0%</span>"
      f"<span><i style='background:{heat_bg(.5)}'></i>50%</span>"
      f"<span><i style='background:{heat_bg(1)}'></i>100%</span>"
      "<span style='opacity:.5'>бледные ячейки — мало примеров (n&lt;3)</span></div>")

    # ---- Where the agent fails (the page's real job) ----
    troubles = []
    for c in top["cases"]:
        st, cause = case_state(c)
        if st not in ("pass", "pending"):   # pending — очередь судьи, не проблема
            troubles.append((c, st, cause))
    troubles.sort(key=lambda t: (0 if t[1] == "broken" else 1, -(t[0].get("elapsed", 0) or 0)))
    if troubles:
        A(f"<h2>Где агент падает <span class=muted style='font-weight:400;font-size:14px'>"
          f"— {len(troubles)} {plural(len(troubles),('проблемный кейс','проблемных кейса','проблемных кейсов'))} у {esc(short_model(top['model']))}</span></h2>")
        A("<div class=fails>")
        for c, st, cause in troubles:
            g, gcls = GLYPH[st]
            key = f"{slugify(top['model'])}__{c['id']}"
            ccls = "broken" if st == "broken" else "fail"
            miss = ("не хватило: " + ", ".join(c.get("missing") or [])) if c.get("missing") else ""
            A(f"<div class=fr data-k='{esc(key)}'>"
              f"<div class='g {gcls}'>{g}</div>"
              f"<div class=body><div class=q>{esc(c['question'])}</div>"
              f"<div class=meta>{esc(CAT_LABEL.get(c['category'],c['category']))} · {c.get('elapsed',0):.0f} с"
              + (f" · {esc(miss)}" if miss else "") + "</div></div>"
              f"<div class='cause {ccls}'>{esc(cause)}</div></div>")
        A("</div>")

    # ---- Per-question matrix ----
    A("<h2>По вопросам <span class=muted style='font-weight:400;font-size:14px'>— клик по строке: вопрос и ответ агента</span></h2>")
    A("<div class=scroll><table><thead><tr><th class=l>Вопрос</th>")
    for b in order:
        A(f"<th>{esc(short_model(b['model']))}</th>")
    A("</tr></thead><tbody>")
    data_js = {}
    for qid in qorder:
        meta = qmeta[qid]
        A("<tr class=qrow>")
        A(f"<td class=l><div class=qcat>{esc(CAT_LABEL.get(meta['category'],meta['category']))}</div>{esc(meta['question'])}</td>")
        for b in order:
            m = b["model"]
            c = cell[m].get(qid)
            if not c:
                A("<td class=muted>—</td>"); continue
            st, cause = case_state(c)
            g, gcls = GLYPH[st]
            key = f"{slugify(m)}__{qid}"
            # /figures/<uuid>.png — файлы прогона, которых больше нет на диске
            # (старый раннер сохранял только счётчик). Мёртвую ссылку не рендерим.
            figs = [(f"shots/{b.get('_dir', slugify(m))}/{f}" if f.startswith("figs/") else f)
                    for f in c.get("figures", []) if not f.startswith("/figures/")]
            bdir = b.get("_dir", slugify(m))
            shot_src = BENCH / bdir / f"{qid}.png"
            data_js[key] = {
                "model": short_model(m), "q": meta["question"], "cat": CAT_LABEL.get(meta["category"], meta["category"]),
                "answer": c.get("answer", ""), "tools": c.get("tools", []), "missing": c.get("missing"),
                "cost": c.get("cost"), "img": c.get("images", 0), "elapsed": c.get("elapsed", 0),
                "figures": figs,
                "trace": [dict(t,
                               result=fmt_tool_result(t["result"]) if t.get("result") else None,
                               call_args=fmt_call_args(t["call_args"]) if t.get("call_args") else None)
                          if (t.get("result") or t.get("call_args")) else t
                          for t in c.get("trace", [])],
                "shot": f"shots/{bdir}/{qid}.png" if shot_src.exists() else None,
                "state": st, "cause": cause,
                # вердикт LLM-судьи (только семантические кейсы): зачёт, score,
                # причина одной строкой, критические дефекты
                "judge_pass": c.get("judge_pass"),
                "judge_score": c.get("judge_score"),
                "judge_reason": c.get("judge_reason"),
                "judge_critical": c.get("judge_critical"),
            }
            A(f"<td><span class='cellmark {gcls}' data-k='{esc(key)}'>{g}</span></td>")
        A("</tr>")
    A("</tbody></table></div>")

    # ---- Methodology (moved to the bottom — reference, not lede) ----
    A("<h2 id=how>Как мы оцениваем</h2>")
    A("<div class=note>«Чисто пройдено» = агент вызвал нужные инструменты <b>и</b> в ответе есть все обязательные ключевые элементы "
      "(чего не хватило — видно в карточке вопроса). Ответы, которых эвал так и не получил "
      f"(модель/API не вернули ничего после ретраев, потолок {CAP_S} с) или оборвались огрызком "
      f"(&lt;{MIN_ANS} символов), считаются <b>оборванными</b> (DNF — эвал не достал ответ, а не модель ошиблась), "
      "а не пройденными — даже если формально совпали по подстроке. "
      "Числовые ответы сверяются по <b>самостоятельному числу</b>, а не по цифре, "
      "случайно совпавшей внутри другого числа, — иначе неверный результат прошёл бы по совпадению. "
      "Вычислительные кейсы (расчёты, графики) грейдит код по точным числам/хэшам; "
      "<b>смысловые</b> (определения, теория, отказы, multi-hop) — <b>состязательные Claude-судьи</b> "
      "(судья → адверсариальный рефутер → критик легитимности): незачёт за галлюцинацию факта, "
      "<b>сломанную формулу</b> (которую KaTeX не отрендерит) и треш-стиль, негодный для учебника. "
      f"Набор: <b>{len(qorder)}</b> {plural(len(qorder),('вопрос','вопроса','вопросов'))} в "
      f"<b>{len(cats)}</b> {plural(len(cats),('категории','категориях','категориях'))}. "
      "Смысловой ответ, который судья ещё не оценил, показывается как <b>⏳ «ждёт судью»</b> и "
      "не участвует ни в числителе, ни в знаменателе — недосуженное никогда не публикуется как провал.")
    if has_cost:
        A(" Стоимость — <b>фактические списания OpenRouter</b> (<code>usage.cost</code>), не оценка по прайсу.")
    A("</div>")

    # ---- Drawer + scrim ----
    A("<div id=scrim></div>")
    A("<div id=detail><button class=x aria-label=Закрыть onclick=\"document.getElementById('detail').classList.remove('open');document.getElementById('scrim').classList.remove('open')\">×</button>"
      "<h3 id=dt></h3><div class=cap style='margin:0 0 8px'>Esc или клик вне окна — закрыть</div>"
      "<div class=meta id=dm></div><div class=toolchips id=dtools></div>"
      "<div class=tracelist id=dtrace></div><div id=dj></div><div class=ans id=da></div></div>")

    # ---- Footer (honest timestamps) ----
    built = time.strftime("%Y-%m-%d %H:%M")
    ran_ats = [b.get("ran_at") for b in benches if b.get("ran_at")]
    if ran_ats:
        d0 = time.strftime("%Y-%m-%d", time.localtime(min(ran_ats)))
        d1 = time.strftime("%Y-%m-%d", time.localtime(max(ran_ats)))
        # честно показываем ДИАПАЗОН: смесь дат — не «один прогон от <max>»
        run_line = f"прогоны {d0} — {d1}" if d0 != d1 else f"прогон от {d1}"
    else:
        run_line = "дата прогона не записана"
    agents = {b.get("agent_commit") for b in benches if b.get("agent_commit")}
    agent_line = ""
    if agents:
        agent_line = (" · агент " + esc(sorted(agents)[0]) if len(agents) == 1
                      else " · <b>⚠ смешаны версии агента: " + esc(", ".join(sorted(agents))) + "</b>")
    A(f"<div class=foot>Страница собрана {built} MSK · {run_line}{agent_line} · "
      "данные: eval/bench_v*/*/bench.json · агент: /assistant/assistant.js (live) · "
      "<a href=\"/\">← к учебнику</a></div>")

    ticons, tlabels, tdot = load_tool_icons()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    dataver = str(int(time.time()))
    (OUT.parent / "data.json").write_text(json.dumps(data_js, ensure_ascii=False), encoding="utf-8")
    A("<script>" + RENDER_JS + DRAWER_JS
      .replace("__DATAVER__", dataver)
      .replace("__ICONS__", json.dumps(ticons, ensure_ascii=False))
      .replace("__LABELS__", json.dumps(tlabels, ensure_ascii=False))
      .replace("__DOT__", json.dumps(tdot, ensure_ascii=False)) + "</script>")
    A("</div></body></html>")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(P), encoding="utf-8")
    # Скрины ответов (eval/bench/<dir>/<case>.png) → docs/benchmark/shots/ —
    # иначе странице нечего показывать: /root никому кроме nginx-копии не виден.
    import shutil
    nshots = 0
    for b in benches:
        bdir = b.get("_dir")
        if not bdir:
            continue
        dst = OUT.parent / "shots" / bdir
        dst.mkdir(parents=True, exist_ok=True)
        for png in list((BENCH / bdir).glob("*.png")) + list((BENCH / bdir).glob("figs/*.png")):
            t = dst / png.relative_to(BENCH / bdir)
            t.parent.mkdir(parents=True, exist_ok=True)
            if not t.exists() or t.stat().st_mtime < png.stat().st_mtime:
                shutil.copy2(png, t)
            nshots += 1
    print(f"wrote {OUT} ({len(benches)} models, {len(qorder)} questions, "
          f"clean={s0['clean']}/{s0['n']}, has_cost={has_cost}, has_tok={has_tok}, shots={nshots})")


def build_all(root="/var/www/sigma/docs/benchmark"):
    """Последняя версия (eval/bench_v<max>) → /benchmark/, остальные → /benchmark/<v>/."""
    vs = [(p.name.replace("bench_", ""), p)
          for p in sorted(EVAL.glob("bench_v*"), key=lambda p: (len(p.name), p.name))]
    if not vs:
        return
    root_out = Path(root) / "index.html"
    latest, _ = vs[-1]
    versions = [(v, "/benchmark/" if v == latest else f"/benchmark/{v}/") for v, _p in vs]
    for v, p in vs:
        out = root_out if v == latest else root_out.parent / v / "index.html"
        build(p, out, version=v, versions=versions)


if __name__ == "__main__":
    # --dev: собрать страницу ТОЛЬКО на dev-стенде. Идущий dev-свип не должен
    # публиковать промежуточную смесь на прод (аудит 2026-07-13, critical #5).
    build_all("/var/www/sigma-dev/docs/benchmark" if "--dev" in sys.argv
              else "/var/www/sigma/docs/benchmark")
