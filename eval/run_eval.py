#!/usr/bin/env python3
"""Full eval runner: replays cases.jsonl against the live sigma assistant
via headless browser, scores against golden, writes markdown report.

Each case (see eval/README.md for schema):
  - tool match: at least one expected tool was called (by name)
  - answer match: all expected_answer_contains substrings present;
                  no expected_answer_excludes substrings present
  - visual match: at least one PNG produced if expected_visual=true

Usage:
    python3 run_eval.py [--cases cases.jsonl] [--base https://sigma.fmin.xyz]
"""

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

from patchright.async_api import async_playwright

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR.parent))
from ru_stem import stem  # noqa: E402  (repo-local Russian stemmer for keyword match)
DEFAULT_CASES = EVAL_DIR / "cases.jsonl"
DEFAULT_BASE = "https://sigma.fmin.xyz"
DEFAULT_CHAPTER_FOR_NOCHAPTER = "ch02_newton"
ASK_TIMEOUT_S = 180  # base cap, RESET forward on progress (see run_one). Quick RAG
# answers finish ≤~45s; this is the idle-without-progress budget, not a wall.
ASK_HARD_CEIL_S = 480  # absolute ceiling even while actively producing output, so a
# heavy multi-tool+Pyodide-plot case can finish (those were the 39 "cutoff" empties)
# while a zero-output hang still bails at the base cap (never makes progress to extend).
EMPTY_RETRIES = 2     # an empty/garbage capture = the eval FAILED to obtain an answer
# (rate-limit / zero-token hang), NOT the model answering wrong → re-ask before giving up.
# Pyodide (numpy/matplotlib) heap is NOT released by .sigma-reset, only by
# tearing down the page. On a 3.8GB box it grows until the chromium renderer
# dies (EPIPE in the patchright driver, June 7 crash on case 24). Recycle the
# whole browser every N cases to keep memory bounded.
RECYCLE_EVERY = 6


def load_cases(path: Path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


async def run_one(page, case):
    """Send the case question to the chat dock; return observed trace."""
    # Hard-reset session: click reset to clear chat history.
    try:
        await page.click(".sigma-reset", timeout=2000)
    except Exception:
        pass

    # Simulate selecting a fragment, if provided. We don't actually highlight
    # text on the page (selecting via the page DOM is fragile across chapters);
    # instead we inject it directly into the assistant state so the model sees
    # the same payload it would when a student actually selects.
    if case.get("fragment"):
        await page.evaluate(
            """(frag) => { window.__sigmaForceFragment = frag; }""",
            case["fragment"],
        )
        # The widget reads selectedFragment from its own state at send time;
        # for the eval we just paste the fragment into the question itself to
        # avoid plumbing. The bigger signal is the question + agent loop.

    await page.fill(".sigma-input", case["question"])
    await page.click(".sigma-send")

    # --- Robust capture: wait for the answer to STOP changing, then read it.
    # The old code read .sigma-answer innerText the instant the spinner cleared,
    # which raced the markdown/KaTeX re-render and captured fragments ('7', 'ям',
    # '', timestamps) on slow/heavy cases — corrupting the grade. Poll until the
    # text is stable (spinner gone + unchanged ~1.2s), or until the timeout.
    probe_js = """() => {
        const bs = document.querySelectorAll('.sigma-bubble-assistant');
        if (!bs.length) return null;
        const last = bs[bs.length - 1];
        const a = last.querySelector('.sigma-answer');
        return {
            streaming: !!last.querySelector('.sigma-status'),
            text: a ? a.innerText : '',
            raw: a ? (a.dataset.raw ?? '') : '',
            traceN: last.querySelectorAll('.sigma-trace-node').length,
        };
    }"""
    # Adaptive deadline: the base cap (ASK_TIMEOUT_S) is enough for a quick RAG
    # answer, but a genuinely-working agentic case (multi-tool + Pyodide plot)
    # can take longer — and the old fixed 180s guillotined those mid-work,
    # recording an EMPTY answer that then scored as a content FAIL. We instead
    # PUSH the deadline forward whenever the agent makes real progress (answer
    # text grew OR a new tool/trace node appeared), capped by ASK_HARD_CEIL_S so
    # a true zero-output hang (free-tier rate-limit) still bails on the base cap
    # without ever producing progress to extend it.
    start = time.monotonic()
    deadline = start + ASK_TIMEOUT_S
    hard_deadline = start + ASK_HARD_CEIL_S
    prev_text, stable_ticks, last = None, 0, None
    last_len, last_trace = 0, 0
    while time.monotonic() < deadline:
        last = await page.evaluate(probe_js)
        text = (last or {}).get("text", "") or ""
        streaming = (last or {}).get("streaming", True)
        trace_n = (last or {}).get("traceN", 0) or 0
        # Progress = real output, NOT the mere presence of the spinner (a hang
        # keeps the spinner on while producing nothing).
        if len(text) > last_len or trace_n > last_trace:
            last_len = max(last_len, len(text))
            last_trace = max(last_trace, trace_n)
            deadline = min(hard_deadline, time.monotonic() + ASK_TIMEOUT_S)
        if (not streaming) and len(text.strip()) > 5 and text == prev_text:
            stable_ticks += 1
            if stable_ticks >= 4:  # ~1.2s unchanged & spinner gone → final answer
                break
        else:
            stable_ticks = 0
        prev_text = text
        await asyncio.sleep(0.3)
    timed_out = (last is None) or last.get("streaming", True) or len((last.get("text") or "").strip()) <= 5

    data = await page.evaluate(
        """() => {
            const bs = document.querySelectorAll('.sigma-bubble-assistant');
            const last = bs[bs.length - 1];
            if (!last) return null;
            const labelMap = {
                'Поиск': 'search_textbook',
                'Глава': 'read_chapter',
                'Оглавление': 'get_outline',
                'Определение': 'find_definition',
                'Теорема': 'find_theorem',
                'Python': 'python',
            };
            const trace = Array.from(last.querySelectorAll('.sigma-trace-node')).map(it => {
                const labelTxt = (it.querySelector('.sigma-trace-label')?.textContent || '').trim();
                return {
                    tool: labelMap[labelTxt] || labelTxt,
                    args: (it.querySelector('.sigma-trace-arg')?.textContent || '').trim(),
                    status: (it.querySelector('.sigma-trace-status')?.textContent || '').trim(),
                };
            });
            const aEl = last.querySelector('.sigma-answer');
            return {
                trace,
                images: last.querySelectorAll('.sigma-figure').length,
                // Capture the RAW model markdown/LaTeX source (stashed on
                // dataset.raw by assistant.js), NOT the KaTeX-rendered innerText
                // (innerText flattens an exponent onto a separate line and drops
                // every dollar sign, so the bench page had nothing to re-render).
                // Fall back to innerText only for old bubbles predating the patch.
                answer: aEl ? (aEl.dataset.raw ?? aEl.innerText ?? '') : '',
            };
        }"""
    ) or {"trace": [], "images": 0, "answer": ""}
    # Prefer the stabilised RAW source — the final evaluate can still catch a
    # re-render. Use the raw markdown (not rendered innerText) for the same reason.
    stable_raw = (last or {}).get("raw") or ""
    if len(stable_raw.strip()) > len((data.get("answer") or "").strip()):
        data["answer"] = stable_raw
    data["timed_out"] = timed_out
    return data


def _norm(s):
    s = str(s).lower()
    s = re.sub(r"[$_{}^\\]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _stems(text):
    return {stem(w) for w in re.findall(r"[а-яёa-z]+", text.lower())}


def _is_garbage(ans):
    """True for empty/fragment/degenerate captures that must never count as a
    real answer (e.g. '', '7', 'ям', '0:00:04.04', a 65k-char run of zeros)."""
    s = (ans or "").strip()
    if len(s) < 15:
        return True
    if re.fullmatch(r"[\d\s:.,%/+\-]+", s):          # pure number / timestamp
        return True
    body = re.sub(r"\s+", "", s)
    if len(body) > 100:
        _, n = Counter(body).most_common(1)[0]
        if n / len(body) > 0.8:                       # one char dominates → loop garbage
            return True
    return False


def _contains(expected, answer_norm, answer_stems):
    """Substring first; then Russian morphology so 'константа' matches
    'константой μ' (every word of the expected phrase present by stem).

    Pure-integer expected is matched as a STANDALONE number token, so "6"
    matches "c = 6" but NOT the 6 inside "16"/"160" — this closes a false-pass
    class where a wrong numeric result still scored because the expected digit
    happened to sit inside some larger number. Long values (≥6 digits, e.g. a
    33-digit factorial) keep the digit-grouping-stripped match so a space-
    grouped "265 252 859…" still satisfies the contiguous "265252859…"."""
    e = _norm(expected)
    if re.fullmatch(r"\d+", e):
        if len(e) >= 6:                                   # long value: tolerate grouping
            return e in re.sub(r"\D", "", answer_norm)
        return re.search(r"(?<!\d)" + e + r"(?!\d)", answer_norm) is not None
    if e and e in answer_norm:
        return True
    words = re.findall(r"[а-яёa-z]+", e)
    return bool(words) and all(stem(w) in answer_stems for w in words)


def score_one(case, obs):
    expected_tools = [t["name"] for t in case.get("expected_tools", [])]
    called_tools = [t["tool"] for t in obs["trace"]]
    # Tool match: substring-tolerant — expected "search" matches called
    # "search_textbook" and vice versa. At least one expected was called.
    def _toolmatch(exp, calls):
        for c in calls:
            if exp == c or exp in c or c in exp:
                return True
        return False
    if expected_tools:
        tool_match = any(_toolmatch(t, called_tools) for t in expected_tools)
    else:
        tool_match = True  # case doesn't require any specific tool

    # Answer match: (1) KaTeX/markdown-tolerant norm, (2) Russian stem matching
    # ('константа'↔'константой'), (3) garbage guard so a mangled/empty capture or
    # a degenerate loop never counts as a real answer.
    answer_norm = _norm(obs["answer"])
    answer_stems = _stems(obs["answer"])
    garbage = _is_garbage(obs["answer"])
    # Render-quality gate: a formula KaTeX can't parse renders as a red error for
    # the reader — that answer is defective and must NOT pass on a mere substring
    # hit. broken_formulas is precomputed (render_gate) and threaded via obs;
    # absent → 0, so legacy callers keep their old behavior.
    broken = int(obs.get("broken_formulas", 0) or 0)
    missing = [s for s in case.get("expected_answer_contains", [])
               if not _contains(s, answer_norm, answer_stems)]
    bad = [s for s in case.get("expected_answer_excludes", [])
           if _norm(s) in answer_norm]
    answer_match = (not missing) and (not bad) and (not garbage) and (broken == 0)

    if case.get("expected_visual"):
        visual_match = obs["images"] > 0
    else:
        visual_match = True

    return {
        "tool_match": tool_match,
        "answer_match": answer_match,
        "visual_match": visual_match,
        "missing": missing,
        "unexpected": bad,
        "garbage": garbage,
        "broken_formulas": broken,
        "timed_out": obs.get("timed_out", False),
        "pass": tool_match and answer_match and visual_match,
    }


def render_report(results, out_md: Path):
    lines = [
        "# Sigma Assistant — Eval Report",
        f"\n_Run: {time.strftime('%Y-%m-%d %H:%M')} MSK · cases: {len(results)}_\n",
    ]

    total = len(results)
    if total == 0:
        lines.append("**Overall: 0/0 pass (no cases ran)**\n")
        out_md.write_text("\n".join(lines), encoding="utf-8")
        return
    passed = sum(1 for r in results if r["score"]["pass"])
    lines.append(f"**Overall: {passed}/{total} pass ({passed*100/total:.0f}%)**\n")

    by_cat = {}
    for r in results:
        by_cat.setdefault(r["case"]["category"], []).append(r)
    lines.append("## Per category\n")
    lines.append("| Category | Pass | Total |")
    lines.append("|---|---:|---:|")
    for cat in sorted(by_cat):
        rs = by_cat[cat]
        p = sum(1 for r in rs if r["score"]["pass"])
        lines.append(f"| `{cat}` | {p} | {len(rs)} |")
    lines.append("")

    lines.append("## Cases\n")
    for r in results:
        c = r["case"]
        s = r["score"]
        mark = "✅" if s["pass"] else "❌"
        lines.append(f"### {mark} `{c['id']}` — {c['category']}\n")
        lines.append(f"**Q:** {c['question']}\n")
        if c.get("chapter_slug"):
            lines.append(f"_Chapter:_ `{c['chapter_slug']}`\n")
        lines.append(f"**Tools called:** {', '.join(t['tool'] for t in r['obs']['trace']) or '(none)'}")
        lines.append(f"**Tools expected:** {', '.join(t['name'] for t in c.get('expected_tools', [])) or '(none)'}")
        lines.append(f"**Images:** {r['obs']['images']} (expected: {'yes' if c.get('expected_visual') else 'no'})")
        lines.append(f"**Elapsed:** {r['elapsed']:.1f}s\n")
        if s["missing"]:
            lines.append(f"_Missing substrings:_ {s['missing']}")
        if s["unexpected"]:
            lines.append(f"_Unexpected substrings:_ {s['unexpected']}")
        lines.append("\n<details><summary>Answer</summary>\n")
        lines.append("\n```\n" + r["obs"]["answer"][:1500] + ("\n…[truncated]" if len(r["obs"]["answer"]) > 1500 else "") + "\n```\n")
        lines.append("</details>\n")
        if r.get("screenshot"):
            lines.append(f"![screenshot]({Path(r['screenshot']).name})\n")
        lines.append("---\n")

    out_md.write_text("\n".join(lines), encoding="utf-8")


async def _launch(p):
    """Fresh browser+context+page. Returned as a tuple so callers can tear the
    whole thing down to reclaim the Pyodide heap."""
    browser = await p.chromium.launch(headless=True)
    ctx = await browser.new_context(viewport={"width": 1200, "height": 900})
    page = await ctx.new_page()
    return browser, ctx, page


async def _probe_case(page, case, base_url):
    """Load the chapter, open the dock, run the case. Raises on browser death
    so the caller can relaunch; returns obs on ordinary (in-page) errors."""
    slug = case.get("chapter_slug") or DEFAULT_CHAPTER_FOR_NOCHAPTER
    url = f"{base_url}/{slug}.html"
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_selector(".sigma-launcher", timeout=10000)
    # open sheet (it persists across navigations actually, but be safe)
    if not await page.is_visible(".sigma-sheet"):
        await page.click(".sigma-launcher")
        await page.wait_for_selector(".sigma-sheet", state="visible", timeout=5000)
    return await run_one(page, case)


def _is_browser_dead(exc):
    """patchright surfaces a dead browser/driver as a 'closed'/EPIPE-ish error.
    These are unrecoverable on the current page → relaunch needed."""
    s = str(exc).lower()
    return any(k in s for k in (
        "target page, context or browser has been closed",
        "browser has been closed", "connection closed", "target closed",
        "pipe", "epipe", "websocket", "transport",
    ))


def _persist(out_dir, results):
    """Write results.jsonl + report.md from whatever we have so far. Called
    after every case AND in finally, so a late crash never loses the run."""
    (out_dir / "results.jsonl").write_text(
        "\n".join(json.dumps({k: v for k, v in r.items() if k != "case"} | {"case_id": r["case"]["id"]},
                              ensure_ascii=False) for r in results),
        encoding="utf-8",
    )
    render_report(results, out_dir / "report.md")


async def run_eval(cases_path: Path, base_url: str, out_dir: Path, only=None):
    cases = load_cases(cases_path)
    if only:
        want = set(only)
        cases = [c for c in cases if c["id"] in want]
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"=== eval run {time.strftime('%Y-%m-%d %H:%M:%S')} · {len(cases)} cases · {base_url} ===")

    results = []
    async with async_playwright() as p:
        browser, ctx, page = await _launch(p)
        try:
            for i, case in enumerate(cases):
                # Proactively recycle the browser to drop accumulated Pyodide heap.
                if i and i % RECYCLE_EVERY == 0:
                    print(f"  [recycle browser after {i} cases]")
                    try:
                        await browser.close()
                    except Exception:
                        pass
                    browser, ctx, page = await _launch(p)

                print(f"\n[{case['id']:<28s}] {case['category']:<14s} → "
                      f"{base_url}/{case.get('chapter_slug') or DEFAULT_CHAPTER_FOR_NOCHAPTER}.html")
                t0 = time.time()
                try:
                    obs = await _probe_case(page, case, base_url)
                except Exception as e:
                    if _is_browser_dead(e):
                        # Browser/driver died mid-case — relaunch and retry once.
                        print(f"  [browser died: {str(e)[:80]} — relaunching, retry]")
                        try:
                            await browser.close()
                        except Exception:
                            pass
                        browser, ctx, page = await _launch(p)
                        try:
                            obs = await _probe_case(page, case, base_url)
                        except Exception as e2:
                            obs = {"trace": [], "images": 0, "answer": f"[probe-error: {e2}]"}
                    else:
                        obs = {"trace": [], "images": 0, "answer": f"[probe-error: {e}]"}

                # Retry on an empty/garbage capture: we obtained NO usable answer,
                # which is an eval failure (transient rate-limit / zero-token hang
                # / mid-work cutoff), NOT the model answering wrong. Re-ask up to
                # EMPTY_RETRIES times — a fresh attempt usually clears the hang.
                attempt = 0
                while _is_garbage(obs.get("answer")) and attempt < EMPTY_RETRIES:
                    attempt += 1
                    print(f"  [empty/garbage capture → retry {attempt}/{EMPTY_RETRIES}]")
                    await asyncio.sleep(5 * attempt)
                    try:
                        retry_obs = await _probe_case(page, case, base_url)
                    except Exception as e:
                        if _is_browser_dead(e):
                            try:
                                await browser.close()
                            except Exception:
                                pass
                            browser, ctx, page = await _launch(p)
                            try:
                                retry_obs = await _probe_case(page, case, base_url)
                            except Exception:
                                retry_obs = None
                        else:
                            retry_obs = None
                    if retry_obs and not _is_garbage(retry_obs.get("answer")):
                        obs = retry_obs
                        break
                    if retry_obs is not None:
                        obs = retry_obs  # keep latest (for trace/diagnostics)
                # no_answer: even after retries we never got a real answer. This is
                # a DNF (eval/API failure), distinct from a wrong/refusing answer —
                # downstream can exclude it instead of scoring it as a content fail.
                obs["no_answer"] = _is_garbage(obs.get("answer"))
                elapsed = time.time() - t0

                score = score_one(case, obs)
                screenshot_path = out_dir / f"{case['id']}.png"
                try:
                    await page.screenshot(path=str(screenshot_path), full_page=False)
                except Exception:
                    screenshot_path = None

                results.append({
                    "case": case,
                    "obs": obs,
                    "score": score,
                    "elapsed": elapsed,
                    "t_start": t0,
                    "t_end": t0 + elapsed,
                    "screenshot": str(screenshot_path) if screenshot_path else None,
                })
                mark = "PASS" if score["pass"] else "FAIL"
                print(f"  {mark}  tools={[t['tool'] for t in obs['trace']]} imgs={obs['images']} miss={score['missing']}")
                # Persist incrementally: a crash on case N+1 keeps cases 1..N.
                _persist(out_dir, results)
        finally:
            # Always leave a (possibly partial) report behind, even if we bailed.
            _persist(out_dir, results)
            if len(results) < len(cases):
                print(f"\n⚠️  PARTIAL: {len(results)}/{len(cases)} cases ran")
            try:
                await browser.close()
            except Exception:
                pass

    print(f"\nreport → {out_dir / 'report.md'}")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(DEFAULT_CASES))
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--out", default=str(EVAL_DIR / "reports" / time.strftime("%Y%m%d_%H%M")))
    ap.add_argument("--only", default=None, help="comma-separated case ids to run (gap-fill)")
    args = ap.parse_args()
    only = [s.strip() for s in args.only.split(",") if s.strip()] if args.only else None
    asyncio.run(run_eval(Path(args.cases), args.base, Path(args.out), only=only))


if __name__ == "__main__":
    main()
