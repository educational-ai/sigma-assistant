#!/usr/bin/env python3
"""Build a structural index of the Sigma textbook from .qmd sources.

Output: /root/sigma_assistant/structural_index.json with shape:
{
  "chapters": [
    {"slug": "ch02_newton", "title": "...", "outline": [{"level": 2, "text": "..."}, ...]}
  ],
  "definitions": [
    {"id": "def_2_3", "chapter_slug": "ch02_newton", "title": "Сверхлинейная...", "text": "...", "line": 380}
  ],
  "theorems": [...],
  "lemmas": [...],
  "examples": [...],
  "algorithms": [...]
}

The index is used by frontend tools find_definition(term), find_theorem(name), outline().
"""

import html as html_module
import json
import re
from pathlib import Path

SIGMA_ROOT = Path("/var/www/sigma")
# Только book/ — то, что реально опубликовано на sigma.fmin.xyz. Каталоги
# 10/ и 11/ (скелеты школьных курсов) никуда не рендерятся: ассистент индексировал
# 9 таких «глав» и цитировал их со ссылками на несуществующие страницы.
CHAPTERS_DIRS = [SIGMA_ROOT / "book"]
SIGMA_DOCS = SIGMA_ROOT / "docs"
SKIP_SLUGS = {"index", "preface"}

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
CALLOUT_OPEN_RE = re.compile(
    r"""^:::\s*\{\.callout-(?P<kind>note|warning|tip|important|caution)
        (?:\s+title=["“](?P<title>[^"”]*)["”])?
        (?:\s+collapse=["“][^"”]*["”])?
        [^}]*\}\s*$""",
    re.MULTILINE | re.VERBOSE,
)
CALLOUT_CLOSE_RE = re.compile(r"^:::\s*$", re.MULTILINE)
HEADING_RE = re.compile(r"^(#{2,4})\s+(.+?)(\s*\{[^}]*\})?\s*$", re.MULTILINE)

HTML_HEADING_RE = re.compile(
    r'<h([2-4])[^>]*\sdata-anchor-id="(?P<anchor>[^"]+)"[^>]*>(?P<inner>.*?)</h\1>',
    re.DOTALL | re.IGNORECASE,
)
TAG_RE = re.compile(r"<[^>]+>")
SECTION_NUMBER_RE = re.compile(r"^[\d.]+\s+")


def _strip_tags(s: str) -> str:
    s = TAG_RE.sub("", s)
    return html_module.unescape(s).strip()


LATEX_COMMAND_WORDS = re.compile(
    r"(?:star|times|cdot|cdots|ldots|vdots|ddots|alpha|beta|gamma|delta|"
    r"epsilon|varepsilon|zeta|eta|theta|vartheta|iota|kappa|lambda|mu|nu|xi|"
    r"pi|varpi|rho|sigma|tau|upsilon|phi|varphi|chi|psi|omega|infty|partial|"
    r"nabla|rightarrow|leftarrow|prime|mathbb|mathcal|mathbf|mathrm)",
    re.IGNORECASE,
)


def _normalize_heading(text: str) -> str:
    # Strip LaTeX inline math ($...$) and backslash commands so .qmd and rendered
    # HTML normalize to the same key (e.g. "$^\star$" vs rendered "^\star").
    text = re.sub(r"\$[^$]*\$", "", text)
    text = re.sub(r"[\\${}^_*`]", "", text)
    text = LATEX_COMMAND_WORDS.sub("", text)
    text = SECTION_NUMBER_RE.sub("", text).strip()
    return re.sub(r"\s+", " ", text).lower()


def load_heading_anchors(slug: str) -> dict[str, str]:
    """Map normalized heading text → anchor-id (from compiled Quarto HTML).

    Returns empty dict if HTML missing — callers fall back to no-anchor URLs.
    """
    path = SIGMA_DOCS / f"{slug}.html"
    if not path.exists():
        return {}
    html = path.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for m in HTML_HEADING_RE.finditer(html):
        anchor = m.group("anchor")
        text = _normalize_heading(_strip_tags(m.group("inner")))
        if text and text not in out:
            out[text] = anchor
    return out


def parse_frontmatter(text: str):
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, text[m.end():]


def extract_callouts(body: str):
    """Yield (kind, title, body_text, line_no) for every callout block."""
    for m in CALLOUT_OPEN_RE.finditer(body):
        kind = m.group("kind")
        title = (m.group("title") or "").strip()
        start = m.end()
        # Find matching close (greedy until first standalone `:::`)
        close = CALLOUT_CLOSE_RE.search(body, start)
        end = close.start() if close else len(body)
        text = body[start:end].strip()
        line_no = body[:m.start()].count("\n") + 1
        yield kind, title, text, line_no


def extract_outline(body: str, anchors: dict[str, str] | None = None):
    out = []
    anchors = anchors or {}
    for m in HEADING_RE.finditer(body):
        level = len(m.group(1))
        text = m.group(2).strip()
        anchor = anchors.get(_normalize_heading(text), "")
        item = {"level": level, "text": text, "line": body[: m.start()].count("\n") + 1}
        if anchor:
            item["anchor"] = anchor
        out.append(item)
    return out


def nearest_heading_anchor(outline: list[dict], line_no: int) -> str:
    """Find anchor of the deepest heading that precedes line_no."""
    best = ""
    for h in outline:
        if h["line"] <= line_no:
            anchor = h.get("anchor", "")
            if anchor:
                best = anchor
        else:
            break
    return best


def classify(kind: str, title: str):
    """Map (callout-kind, title) to a structural category."""
    t = title.lower()
    if "определение" in t or "definition" in t:
        return "definitions"
    if "теорема" in t or "theorem" in t:
        return "theorems"
    if "лемма" in t or "lemma" in t:
        return "lemmas"
    if "следствие" in t or "corollary" in t:
        return "corollaries"
    if "алгоритм" in t or "algorithm" in t:
        return "algorithms"
    if "пример" in t or "example" in t:
        return "examples"
    if "задач" in t and "самостоятель" in t:
        return "exercises"
    if "историческая" in t or "history" in t:
        return "history"
    return None  # uncategorized — skip from structural index


def main():
    chapters = []
    by_category = {
        k: []
        for k in (
            "definitions",
            "theorems",
            "lemmas",
            "corollaries",
            "algorithms",
            "examples",
            "exercises",
            "history",
        )
    }
    seen_slugs = set()

    for d in CHAPTERS_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.qmd")):
            slug = p.stem
            if slug in SKIP_SLUGS or slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            raw = p.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(raw)
            title = meta.get("title") or slug.replace("_", " ")

            anchors = load_heading_anchors(slug)
            outline = extract_outline(body, anchors)
            chapters.append({
                "slug": slug,
                "title": title,
                "path": str(p),
                "outline": outline,
            })

            for kind, ctitle, text, line_no in extract_callouts(body):
                cat = classify(kind, ctitle)
                if not cat:
                    continue
                # Generate a stable id: <cat-prefix>_<slug-short>_<line>
                cat_prefix = cat[:3]
                short_slug = re.sub(r"[^a-z0-9]+", "", slug.lower())[:12]
                item_id = f"{cat_prefix}_{short_slug}_{line_no}"
                anchor = nearest_heading_anchor(outline, line_no)
                entry = {
                    "id": item_id,
                    "chapter_slug": slug,
                    "chapter_title": title,
                    "title": ctitle,
                    "text": text,
                    "line": line_no,
                }
                if anchor:
                    entry["anchor"] = anchor
                by_category[cat].append(entry)

    out = {"chapters": chapters, **by_category}
    Path("/root/sigma_assistant/structural_index.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Chapters: {len(chapters)}")
    for cat, items in by_category.items():
        print(f"  {cat:14s} {len(items):4d}")


if __name__ == "__main__":
    main()
