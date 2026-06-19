"""Render-quality gate: count broken formulas in a benchmark answer.

A model answer is stored as RAW markdown/LaTeX (dataset.raw capture). Before it
can count as a clean pass, it must actually RENDER for the reader — a formula
KaTeX can't parse shows as a red .katex-error on the page. This module batches
all answers through eval/validate_render.js (node + the real KaTeX) and returns
the broken-formula count per id, so the grader can fail defective answers no
matter how well their substring matches the expected keywords.
"""
import json, re, subprocess
from pathlib import Path

_JS = Path(__file__).resolve().parent / "validate_render.js"

# The site's renderMarkdown renders ONLY $…$ and $$…$$. LaTeX written with the
# \(…\) / \[…\] delimiters is NOT a formula to the page — it passes through
# escapeHtml and the reader sees raw backslash-LaTeX (just as bad as a
# .katex-error). validate_render.js can't see this (it only extracts $-formulas),
# so we count raw delimiters here in Python and surface them as `raw_unrendered`.
# An answer that delivers its math predominantly through these delimiters renders
# as garbage on the page and must not score as a clean pass (incident 2026-06-10:
# seed-1.6-flash / ministral-8b / mistral-small emit \(…\) wholesale).
_DISP = re.compile(r"\$\$[\s\S]+?\$\$")
_INL = re.compile(r"(?<![$\\])\$[^\n$]+?\$(?!\$)")
_RAW_PAREN = re.compile(r"\\\([\s\S]+?\\\)")
_RAW_BRACK = re.compile(r"\\\[[\s\S]+?\\\]")


def _delim_counts(answer):
    """(dollar, raw): # of $-rendered formulas vs # of raw \\(…\\)/\\[…\\] the
    page will NOT render."""
    a = answer or ""
    masked = _DISP.sub("  ", a)
    dollar = len(_DISP.findall(a)) + len(_INL.findall(masked))
    raw = len(_RAW_PAREN.findall(a)) + len(_RAW_BRACK.findall(a))
    return dollar, raw


def broken_batch(items):
    """items: iterable of (id, answer). Returns {id: {"formulas":N,"broken":M,
    "broken_exprs":[...], "dollar":D, "raw_unrendered":R}}. On any node failure
    the $-validation falls back to zeros (fail-open), but the raw-delimiter count
    (pure Python) is always computed."""
    items = [{"id": str(i), "answer": a or ""} for i, a in items]
    if not items:
        return {}
    delims = {it["id"]: _delim_counts(it["answer"]) for it in items}
    try:
        p = subprocess.run(
            ["node", str(_JS)],
            input=json.dumps({"items": items}),
            capture_output=True, text=True, timeout=120,
        )
        if p.returncode != 0:
            res = {it["id"]: {"formulas": 0, "broken": 0, "broken_exprs": []} for it in items}
        else:
            res = json.loads(p.stdout)
    except Exception:
        res = {it["id"]: {"formulas": 0, "broken": 0, "broken_exprs": []} for it in items}
    for k, (d, r) in delims.items():
        res.setdefault(k, {"formulas": 0, "broken": 0, "broken_exprs": []})
        res[k]["dollar"] = d
        res[k]["raw_unrendered"] = r
    return res


def raw_render_defective(dollar, raw):
    """True when the answer's math is predominantly delivered through delimiters
    the site won't render → reader sees raw LaTeX. Threshold: ≥2 raw formulas and
    raw ≥ dollar (calibrated against the confirmed-garbage vs incidental-leak
    anchors, 2026-06-10)."""
    return raw >= 2 and raw >= dollar


def broken_count(answer):
    """Single-answer convenience → int broken count."""
    r = broken_batch([("x", answer)])
    return int(r.get("x", {}).get("broken", 0))
