#!/usr/bin/env python3
"""Render each benchmark answer to a PNG exactly as a reader sees it on the
page — same renderMarkdown + KaTeX + .ans CSS as gen_benchmark_page — so a
VISION judge can score visual adequacy (broken formulas, garbled line breaks,
collapsed tables, trash layout) that a text/substring grader is blind to.

Usage:
  python3 render_answer_shots.py <bench.json> [out_dir]
  python3 render_answer_shots.py --all            # every eval/bench/*/bench.json

Writes <out_dir>/<case_id>.png and an index.jsonl {id, png, formulas, broken}.
"""
import sys, json, asyncio
from pathlib import Path

EVAL = Path(__file__).resolve().parent
ROOT = EVAL.parent
sys.path.insert(0, str(ROOT))
import gen_benchmark_page as G  # RENDER_JS + CSS, reused verbatim
from patchright.async_api import async_playwright

KATEX = "https://cdn.jsdelivr.net/npm/katex@0.16.11/dist"

PAGE = """<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<link rel=stylesheet href="%s/katex.min.css">
<script defer src="%s/katex.min.js"></script>
<style>%s
body{margin:0;background:#fff}
/* unhide the drawer container so the .ans card lays out on-screen */
#detail{position:static!important;transform:none!important;max-height:none!important;
  box-shadow:none;border:none;padding:0;width:760px}
#wrap{padding:18px}
</style></head><body><div id=wrap><div id=detail><div class="ans" id="da"></div></div></div>
<script>%s</script></body></html>""" % (KATEX, KATEX, G.CSS, G.RENDER_JS)


async def shoot(answers, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    index = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 800, "height": 600}, device_scale_factor=2)
        await page.set_content(PAGE, wait_until="load")
        # katex.min.js is `defer` — wait until window.katex is live before rendering.
        await page.wait_for_function("() => !!window.katex && !!window.renderMarkdown", timeout=15000)
        for cid, ans in answers:
            res = await page.evaluate(
                """(ans) => {
                    const da = document.getElementById('da');
                    da.innerHTML = ans ? renderMarkdown(ans) : '(пустой ответ)';
                    return { broken: da.querySelectorAll('.katex-error').length,
                             formulas: da.querySelectorAll('.katex').length };
                }""", ans or "")
            png = out_dir / f"{cid}.png"
            await page.locator("#detail").screenshot(path=str(png))
            index.append({"id": cid, "png": str(png), "formulas": res["formulas"], "broken": res["broken"]})
        await browser.close()
    (out_dir / "index.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in index), encoding="utf-8")
    return index


def answers_from_bench(bj: Path):
    b = json.loads(bj.read_text(encoding="utf-8"))
    return [(c["id"], c.get("answer", "") or "") for c in b["cases"]]


def main():
    args = sys.argv[1:]
    if args and args[0] == "--all":
        for bj in sorted(sorted(EVAL.glob('bench_v*'), key=lambda p: (len(p.name), p.name))[-1].glob('*/bench.json')):
            out = EVAL / "shots" / bj.parent.name
            idx = asyncio.run(shoot(answers_from_bench(bj), out))
            nb = sum(1 for r in idx if r["broken"])
            print(f"{bj.parent.name:36} {len(idx)} shots, {nb} with broken formulas → {out}")
        return
    bj = Path(args[0])
    out = Path(args[1]) if len(args) > 1 else EVAL / "shots" / bj.parent.name
    idx = asyncio.run(shoot(answers_from_bench(bj), out))
    nb = sum(1 for r in idx if r["broken"])
    print(f"{len(idx)} shots, {nb} with broken formulas → {out}")


if __name__ == "__main__":
    main()
