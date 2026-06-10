"""Focused screenshot of the trace tree only — for visual inspection.
Runs separate questions and captures just the assistant bubble area."""
import asyncio
from pathlib import Path
from patchright.async_api import async_playwright

URL = "https://sigma.fmin.xyz/ch02_newton.html"
OUT = Path("/root/sigma_assistant/eval/reports/trace_focus")
OUT.mkdir(parents=True, exist_ok=True)

SCENARIOS = [
    ("01_definition", "Что такое сильно выпуклая функция?", 60_000),
    ("02_history", "Кто такой Канторович и за что Нобелевскую премию?", 60_000),
    ("03_theorem", "Сформулируй теорему о сходимости метода Герона.", 60_000),
    ("04_python_simple", "Посчитай 25 факториал через python.", 150_000),
    ("05_multistep", "В чём отличие Ньютона от градиентного спуска? Какие главы про них есть в учебнике?", 90_000),
]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 1100}, device_scale_factor=2)
        page = await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_selector(".sigma-launcher", timeout=10_000)
        if not await page.is_visible(".sigma-sheet"):
            await page.click(".sigma-launcher")
            await page.wait_for_selector(".sigma-sheet", state="visible", timeout=5_000)
        for slug, q, t in SCENARIOS:
            try:
                await page.click(".sigma-reset", timeout=2_000)
            except Exception:
                pass
            await page.fill(".sigma-input", q)
            await page.click(".sigma-send")
            try:
                await page.wait_for_function(
                    """() => {
                        const bs = document.querySelectorAll('.sigma-bubble-assistant');
                        if (!bs.length) return false;
                        const last = bs[bs.length - 1];
                        if (last.querySelector('.sigma-status')) return false;
                        const a = last.querySelector('.sigma-answer');
                        return a && a.innerText.length > 5;
                    }""",
                    timeout=t,
                )
            except Exception:
                pass
            try:
                el = await page.query_selector(".sigma-bubble-assistant:last-child")
                if el:
                    await el.screenshot(path=str(OUT / f"{slug}.png"))
                    print(f"  {slug} captured (bubble)")
                else:
                    await page.screenshot(path=str(OUT / f"{slug}_full.png"), full_page=False)
                    print(f"  {slug} captured (full)")
            except Exception as e:
                print(f"  {slug} screenshot failed: {e}")
        # also capture collapsed trace state
        try:
            await page.click(".sigma-trace-summary", timeout=2_000)
            await page.wait_for_timeout(300)
            el = await page.query_selector(".sigma-bubble-assistant:last-child")
            if el:
                await el.screenshot(path=str(OUT / "06_collapsed.png"))
                print("  06_collapsed captured")
        except Exception as e:
            print(f"  collapsed: {e}")
        await browser.close()

asyncio.run(main())
