#!/usr/bin/env python3
"""Quick end-to-end probe: open a chapter in headless Chromium, ask the
assistant a few questions, capture the tool trace + answer + screenshot.

Run:
    python3 headless_probe.py [--url https://sigma.fmin.xyz/ch02_newton.html]
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from patchright.async_api import async_playwright

DEFAULT_URL = "https://sigma.fmin.xyz/ch02_newton.html"

PROBE_QUESTIONS = [
    {
        "id": "kantorovich",
        "question": "Когда Канторович получил Нобелевскую премию и за что?",
        "expect_tools": ["search_textbook", "find_definition", "find_theorem"],
        "expect_substring": ["1975"],
    },
    {
        "id": "factorial",
        "question": "Сколько будет 30!? Посчитай через python.",
        "expect_tools": ["python"],
        "expect_substring": ["26525285981219105863630848"],
    },
    {
        "id": "plot_newton",
        "question": "Покажи график траектории метода Ньютона для f(x)=x^2-2 из x0=1.5, 6 итераций.",
        "expect_tools": ["python"],
        "expect_substring": ["1.41"],
        "expect_image": True,
    },
]


async def run_probe(url: str, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1200, "height": 900})
        page = await ctx.new_page()

        # Forward browser console for debugging
        page.on("console", lambda msg: print(f"  [console {msg.type}] {msg.text}", file=sys.stderr))
        page.on("pageerror", lambda exc: print(f"  [pageerror] {exc}", file=sys.stderr))

        print(f"opening {url}…")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector(".sigma-launcher", timeout=10000)
        await page.click(".sigma-launcher")
        await page.wait_for_selector(".sigma-sheet", state="visible", timeout=5000)

        for case in PROBE_QUESTIONS:
            print(f"\n--- {case['id']}: {case['question'][:60]}")
            t0 = time.time()
            await page.fill(".sigma-input", case["question"])
            await page.click(".sigma-send")

            # Wait for an assistant bubble to appear and finish (status removed).
            try:
                await page.wait_for_function(
                    """() => {
                        const bubbles = document.querySelectorAll('.sigma-bubble-assistant');
                        if (!bubbles.length) return false;
                        const last = bubbles[bubbles.length - 1];
                        const status = last.querySelector('.sigma-status');
                        const answer = last.querySelector('.sigma-answer');
                        return !status && answer && answer.innerText.length > 5;
                    }""",
                    timeout=180000,
                )
            except Exception as e:
                print(f"  TIMEOUT or error waiting for answer: {e}")

            elapsed = time.time() - t0

            # Inspect the last assistant bubble.
            data = await page.evaluate(
                """() => {
                    const bubbles = document.querySelectorAll('.sigma-bubble-assistant');
                    const last = bubbles[bubbles.length - 1];
                    if (!last) return null;
                    const traceItems = Array.from(last.querySelectorAll('.sigma-trace-item')).map(it => ({
                        tool: it.querySelector('.sigma-trace-tool')?.textContent || '',
                        args: it.querySelector('.sigma-trace-args')?.textContent || '',
                        status: it.querySelector('.sigma-trace-status')?.textContent || '',
                    }));
                    const images = last.querySelectorAll('.sigma-figure').length;
                    const answer = last.querySelector('.sigma-answer')?.innerText || '';
                    return { trace: traceItems, images, answer };
                }"""
            )

            if not data:
                print("  ✗ no assistant bubble found")
                continue

            tools_called = [t["tool"] for t in data["trace"]]
            expect_tools = set(case.get("expect_tools", []))
            tool_hit = bool(expect_tools & set(tools_called))
            ans_hits = [s for s in case.get("expect_substring", []) if s.lower() in data["answer"].lower()]
            ans_ok = len(ans_hits) == len(case.get("expect_substring", []))
            img_ok = (data["images"] > 0) if case.get("expect_image") else True

            ok = tool_hit and ans_ok and img_ok
            mark = "✓" if ok else "✗"
            print(f"  {mark} elapsed {elapsed:.1f}s | tools={tools_called} | images={data['images']}")
            print(f"  ans head: {data['answer'][:200]}")
            if not ans_ok:
                print(f"  MISSING: {[s for s in case['expect_substring'] if s.lower() not in data['answer'].lower()]}")

            # Screenshot per case
            screenshot_path = out_dir / f"{case['id']}.png"
            await page.screenshot(path=str(screenshot_path), full_page=False)

            results.append({
                "id": case["id"],
                "question": case["question"],
                "tools_called": tools_called,
                "expected_tools": list(expect_tools),
                "tool_hit": tool_hit,
                "answer": data["answer"],
                "answer_ok": ans_ok,
                "images": data["images"],
                "image_ok": img_ok,
                "elapsed": elapsed,
                "screenshot": str(screenshot_path),
                "pass": ok,
            })

        await browser.close()

    # Write JSONL log
    log_path = out_dir / "probe.jsonl"
    log_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results), encoding="utf-8")
    print(f"\n=== summary ===")
    for r in results:
        print(f"  {'PASS' if r['pass'] else 'FAIL'}  {r['id']:<14s}  {r['elapsed']:5.1f}s  tools={r['tools_called']}")
    print(f"\nresults → {log_path}")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--out", default="/root/sigma_assistant/eval/reports/probe_" + time.strftime("%Y%m%d_%H%M"))
    args = ap.parse_args()
    asyncio.run(run_probe(args.url, Path(args.out)))


if __name__ == "__main__":
    main()
