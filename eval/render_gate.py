"""Render-quality gate: count broken formulas in a benchmark answer.

A model answer is stored as RAW markdown/LaTeX (dataset.raw capture). Before it
can count as a clean pass, it must actually RENDER for the reader — a formula
KaTeX can't parse shows as a red .katex-error on the page. This module batches
all answers through eval/validate_render.js (node + the real KaTeX) and returns
the broken-formula count per id, so the grader can fail defective answers no
matter how well their substring matches the expected keywords.
"""
import json, subprocess
from pathlib import Path

_JS = Path(__file__).resolve().parent / "validate_render.js"


def broken_batch(items):
    """items: iterable of (id, answer). Returns {id: {"formulas":N,"broken":M,
    "broken_exprs":[...]}}. On any failure returns zeros (fail-open: never let a
    validator hiccup silently fail every model)."""
    items = [{"id": str(i), "answer": a or ""} for i, a in items]
    if not items:
        return {}
    try:
        p = subprocess.run(
            ["node", str(_JS)],
            input=json.dumps({"items": items}),
            capture_output=True, text=True, timeout=120,
        )
        if p.returncode != 0:
            return {it["id"]: {"formulas": 0, "broken": 0, "broken_exprs": []} for it in items}
        return json.loads(p.stdout)
    except Exception:
        return {it["id"]: {"formulas": 0, "broken": 0, "broken_exprs": []} for it in items}


def broken_count(answer):
    """Single-answer convenience → int broken count."""
    r = broken_batch([("x", answer)])
    return int(r.get("x", {}).get("broken", 0))
