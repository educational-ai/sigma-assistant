#!/usr/bin/env python3
"""Sigma textbook AI assistant — thin backend.

The agent loop lives in the BROWSER (assistant.js). This backend is a
narrow set of HTTP services the browser-side agent calls into:

  GET  /api/model                    → current top free model id (cached 5m)
  POST /api/llm                      → streaming SSE proxy to OpenRouter
  POST /api/textbook/search          → keyword search over chunks
  POST /api/textbook/read            → fetch chapter body (or section)
  GET  /api/textbook/outline         → all chapters + h2/h3 outline
  POST /api/textbook/find_definition → lookup definition by term
  POST /api/textbook/find_theorem    → lookup theorem by name/keyword
  GET  /healthz                      → {ok, chunks, defs, thms}

No agent loop, no JSON extraction, no LaTeX repair — the browser uses
OpenRouter's native tool_calls format directly. We only proxy bytes so
the API key never leaks to the client.
"""

import html as _html_mod
import http.server
import json
import os
import re
import socketserver
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

SIGMA_ROOT = Path("/var/www/sigma")
CHAPTERS_DIRS = [SIGMA_ROOT / "book", SIGMA_ROOT / "10", SIGMA_ROOT / "11"]
SKIP_SLUGS = {"index", "preface"}

STRUCTURAL_INDEX_PATH = Path("/root/sigma_assistant/structural_index.json")

API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
MODEL_ENDPOINT = "https://shir-man.com/api/free-llm/top-models"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
FALLBACK_MODEL = "openrouter/free"

# Override: если SIGMA_MODEL задан, используем эту конкретную платную модель
# и игнорируем shir-man-ранкер free-моделей (которые медленные и нестабильные).
# Для vision-запросов используем SIGMA_VISION_MODEL (если задан), иначе ту же.
PAID_MODEL = os.environ.get("SIGMA_MODEL", "").strip()
PAID_VISION_MODEL = os.environ.get("SIGMA_VISION_MODEL", "").strip() or PAID_MODEL

# ---------------------------------------------------------------------------
# GigaChat (Sber) — alternative upstream for benchmarking the SAME agent on
# GigaChat models. Activated only when SIGMA_MODEL is a GigaChat id; the
# OpenRouter path is untouched. GigaChat speaks the OpenAI-legacy `functions` /
# `function_call` dialect (not `tools`/`tool_calls`) and streams its own way, so
# this proxy translates in BOTH directions: we call GigaChat NON-streaming and
# re-emit a synthetic OpenAI SSE stream the browser agent (assistant.js) already
# understands. SSL: the Russian-Trust CA isn't in the system store → unverified
# context (same as `curl -k`).
# ---------------------------------------------------------------------------
GIGACHAT_AUTH_KEY = os.environ.get("GIGACHAT_AUTH_KEY", "").strip()
GIGACHAT_SCOPE = os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_CORP").strip()
GIGACHAT_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
_GIGA_SSL = ssl.create_default_context()
_GIGA_SSL.check_hostname = False
_GIGA_SSL.verify_mode = ssl.CERT_NONE
_giga_token = {"tok": None, "exp": 0.0}

# Price ₽ per 1000 tokens (Sber legal-entity tariffs, eff. 2026-02-01). GigaChat
# bills TOTAL tokens at a flat rate (no input/output split). Converted to USD on
# the bench page at RUB_PER_USD so it shares the cost axis with OpenRouter models.
GIGACHAT_PRICE_RUB_PER_1K = {
    "GigaChat": 0.065, "GigaChat-2": 0.065, "GigaChat-Plus": 0.065,
    "GigaChat-Pro": 0.5, "GigaChat-2-Pro": 0.5,
    "GigaChat-Max": 0.65, "GigaChat-2-Max": 0.65,
}
GIGACHAT_RUB_PER_USD = 80.0


def _is_gigachat_model(model_id: str) -> bool:
    return (model_id or "").strip().lower().startswith("gigachat")


def get_gigachat_token() -> str:
    """Fetch+cache the 30-min OAuth access token (refresh ~1 min before expiry)."""
    now = time.time()
    if _giga_token["tok"] and now < _giga_token["exp"] - 60:
        return _giga_token["tok"]
    data = urllib.parse.urlencode({"scope": GIGACHAT_SCOPE}).encode("utf-8")
    req = urllib.request.Request(
        GIGACHAT_OAUTH_URL, data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "Authorization": f"Basic {GIGACHAT_AUTH_KEY}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, context=_GIGA_SSL, timeout=30) as r:
        j = json.loads(r.read())
    _giga_token["tok"] = j["access_token"]
    # expires_at is unix-ms; fall back to now+25min if absent.
    _giga_token["exp"] = (j.get("expires_at", 0) / 1000.0) or (now + 1500)
    return _giga_token["tok"]


def _flatten_content(content):
    """OpenAI multimodal content (list of {type,text|image_url}) → plain text.
    GigaChat chat API is text-only here; image parts are dropped (graphics/vision
    cases will honestly fail, like any text-only model in the board)."""
    if isinstance(content, list):
        return "\n".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    if content is None:
        return ""
    return content if isinstance(content, str) else str(content)


def build_gigachat_payload(client_body: dict) -> dict:
    """Translate the OpenAI-shaped client body into GigaChat's `functions` dialect."""
    msgs = client_body.get("messages") or []
    # tool_call_id → function name, recovered from assistant tool_calls, so a
    # `role:tool` result can become a `role:function` message GigaChat accepts.
    id2name = {}
    for m in msgs:
        if m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                cid, fn = tc.get("id"), (tc.get("function") or {}).get("name")
                if cid and fn:
                    id2name[cid] = fn
    out_msgs = []
    for m in msgs:
        role = m.get("role")
        if role == "tool":
            name = id2name.get(m.get("tool_call_id")) or m.get("name") or "func"
            content = _flatten_content(m.get("content"))
            # GigaChat REQUIRES a function result's content to be a VALID JSON
            # string and rejects the whole request with HTTP 422 otherwise. Our
            # tool results are JSON but get char-truncated upstream (TOOL_CHAR_LIMIT)
            # → invalid JSON → 422 → the entire tool loop dies and the model
            # answers ungrounded (a measurement artifact, not a model failure).
            # Coerce to guaranteed-valid JSON: pass through if it already parses,
            # else wrap the (possibly-truncated) text as {"text": ...}.
            try:
                json.loads(content)
                safe = content
            except Exception:
                safe = json.dumps({"text": content}, ensure_ascii=False)
            out_msgs.append({"role": "function", "name": name, "content": safe})
        elif role == "assistant" and m.get("tool_calls"):
            tc = m["tool_calls"][0]  # GigaChat carries one function_call per turn
            fn = tc.get("function") or {}
            raw = fn.get("arguments")
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except Exception:
                args = {}
            out_msgs.append({"role": "assistant",
                             "content": _flatten_content(m.get("content")) or "",
                             "function_call": {"name": fn.get("name", ""), "arguments": args}})
        else:
            out_msgs.append({"role": role, "content": _flatten_content(m.get("content"))})
    payload = {
        "model": client_body.get("model") or PAID_MODEL,
        "messages": out_msgs,
        "stream": False,
    }
    t = client_body.get("temperature")
    if t is not None:
        payload["temperature"] = max(float(t), 0.01)  # GigaChat wants temp > 0
    tools = client_body.get("tools")
    if tools:
        funcs = [(td.get("function") or {}) for td in tools if td.get("type") == "function"]
        funcs = [f for f in funcs if f.get("name")]
        if funcs:
            payload["functions"] = funcs
            payload["function_call"] = "auto"
    return payload



# ---------------------------------------------------------------------------
# Model fetch (cached) — text and vision variants share the shir-man ranker;
# vision = first shir-man top with `image` in its OpenRouter input modalities,
# else fall back to the highest-context :free image-capable model from OpenRouter.
# No hardcoded ids anywhere.
# ---------------------------------------------------------------------------

_model_cache = {"text": None, "vision": None, "vision_chain": [], "fetched_at": 0.0}
_modalities_cache = {"map": None, "fetched_at": 0.0}
_bad_models: dict[str, float] = {}  # id → unix ts; skip for 10 min after 502
BAD_TTL = 600
MODEL_TTL = 300       # 5 min — shir-man already updates daily
MODALITIES_TTL = 3600 # 1h — OpenRouter catalogue rarely changes


def _http_json(url: str, timeout: int = 6):
    req = urllib.request.Request(
        url, headers={"User-Agent": "sigma-assistant/1.0 (+sigma.fmin.xyz)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fetch_modalities() -> dict[str, list[str]]:
    """`{model_id: [input_modalities]}` for every OpenRouter model. Cached 1h."""
    now = time.time()
    if _modalities_cache["map"] and now - _modalities_cache["fetched_at"] < MODALITIES_TTL:
        return _modalities_cache["map"]
    try:
        data = _http_json(OPENROUTER_MODELS_URL)
        result: dict[str, list[str]] = {}
        for m in data.get("data", []):
            mid = m.get("id")
            arch = m.get("architecture") or {}
            mods = arch.get("input_modalities") or []
            if mid:
                result[mid] = list(mods)
        _modalities_cache.update(map=result, fetched_at=now)
        return result
    except Exception as e:
        print(f"[modalities] fetch failed: {e}", file=sys.stderr)
        return _modalities_cache["map"] or {}


def _is_free(model: dict) -> bool:
    """Treat a model as free if its id is suffixed :free or its prompt price is 0."""
    mid = model.get("id", "")
    if mid.endswith(":free"):
        return True
    pricing = model.get("pricing") or {}
    try:
        return float(pricing.get("prompt", 1) or 0) == 0.0
    except (TypeError, ValueError):
        return False


def _catalogue_vision_chain() -> list[str]:
    """Free, tool-capable, image-in chat models from OpenRouter, ranked by ctx desc.

    Positive filter: model must support `tools` and `tool_choice` (so it can
    drive the agent loop) and accept `image` as input. Excludes generation-only
    free models (Lyria etc.) that are zero-priced but can't tool-call.
    """
    try:
        data = _http_json(OPENROUTER_MODELS_URL)
        candidates = []
        for m in data.get("data", []):
            arch = m.get("architecture") or {}
            if "image" not in (arch.get("input_modalities") or []):
                continue
            supported = set(m.get("supported_parameters") or [])
            if "tools" not in supported:
                continue
            if not _is_free(m):
                continue
            ctx = int(m.get("context_length") or 0)
            candidates.append((ctx, m["id"]))
        candidates.sort(reverse=True)
        return [mid for _, mid in candidates]
    except Exception as e:
        print(f"[vision-catalogue] {e}", file=sys.stderr)
        return []


def _refresh_models() -> None:
    """Populate _model_cache with text top + ordered vision retry chain."""
    now = time.time()
    if _model_cache["text"] and now - _model_cache["fetched_at"] < MODEL_TTL:
        return
    try:
        data = _http_json(MODEL_ENDPOINT, timeout=5)
        ranked = [m.get("id") for m in data.get("models", []) if m.get("id")]
        if not ranked:
            raise ValueError("no models in shir-man response")
        mods = fetch_modalities()
        # Vision chain: shir-man's vision-capable models first (ordered by their
        # ranking), then the rest of the catalogue's free vision models (by ctx).
        shir_vision = [mid for mid in ranked if "image" in (mods.get(mid) or [])]
        catalogue = _catalogue_vision_chain()
        chain: list[str] = []
        for mid in shir_vision + catalogue:
            if mid not in chain:
                chain.append(mid)
        if not chain:
            chain = [FALLBACK_MODEL]
        _model_cache.update(
            text=ranked[0],
            vision=chain[0],
            vision_chain=chain,
            fetched_at=now,
        )
    except Exception as e:
        print(f"[model] fetch failed: {e}", file=sys.stderr)
        if not _model_cache["text"]:
            _model_cache["text"] = FALLBACK_MODEL
            _model_cache["vision"] = FALLBACK_MODEL
            _model_cache["vision_chain"] = [FALLBACK_MODEL]


def fetch_top_model(vision: bool = False) -> str:
    _refresh_models()
    if vision:
        return _next_vision_model() or _model_cache["text"]
    return _model_cache["text"]


def _next_vision_model() -> str | None:
    """First model in the vision chain that isn't quarantined."""
    now = time.time()
    for mid in _model_cache.get("vision_chain") or []:
        bad_until = _bad_models.get(mid, 0.0)
        if now >= bad_until:
            return mid
    # All quarantined → pick the one expiring soonest (least bad).
    chain = _model_cache.get("vision_chain") or []
    return min(chain, key=lambda m: _bad_models.get(m, 0.0), default=None)


def mark_model_bad(model_id: str) -> None:
    if model_id:
        _bad_models[model_id] = time.time() + BAD_TTL
        print(f"[model] quarantined {model_id} for {BAD_TTL}s", file=sys.stderr)


# ---------------------------------------------------------------------------
# .qmd parsing + chunk index
# ---------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
CALLOUT_OPEN_RE = re.compile(r":::\s*\{\.callout[^}]*\}\s*\n", re.IGNORECASE)
FENCED_DIV_RE = re.compile(r":::[\w.\- ]*", re.IGNORECASE)
TOKEN_RE = re.compile(r"\w+", re.UNICODE)
SECTION_RE = re.compile(r"^(#{2,4})\s+(.+?)(\s*\{[^}]*\})?\s*$", re.MULTILINE)
HTML_HEADING_RE = re.compile(
    r'<h([2-4])[^>]*\sdata-anchor-id="(?P<anchor>[^"]+)"[^>]*>(?P<inner>.*?)</h\1>',
    re.DOTALL | re.IGNORECASE,
)
HTML_TAG_RE = re.compile(r"<[^>]+>")
SECTION_NUMBER_RE = re.compile(r"^[\d.]+\s+")
SIGMA_DOCS = Path("/var/www/sigma/docs")


LATEX_COMMAND_WORDS = re.compile(
    r"(?:star|times|cdot|cdots|ldots|vdots|ddots|alpha|beta|gamma|delta|"
    r"epsilon|varepsilon|zeta|eta|theta|vartheta|iota|kappa|lambda|mu|nu|xi|"
    r"pi|varpi|rho|sigma|tau|upsilon|phi|varphi|chi|psi|omega|infty|partial|"
    r"nabla|rightarrow|leftarrow|prime|mathbb|mathcal|mathbf|mathrm)",
    re.IGNORECASE,
)


def _normalize_heading(text: str) -> str:
    text = HTML_TAG_RE.sub("", text)
    text = _html_mod.unescape(text)
    text = re.sub(r"\$[^$]*\$", "", text)
    text = re.sub(r"[\\${}^_*`]", "", text)
    text = LATEX_COMMAND_WORDS.sub("", text)
    text = SECTION_NUMBER_RE.sub("", text).strip()
    return re.sub(r"\s+", " ", text).lower()


_heading_anchors_cache: dict[str, dict[str, str]] = {}


def load_heading_anchors(slug: str) -> dict[str, str]:
    """Map normalized heading text → anchor-id, read from compiled Quarto HTML."""
    if slug in _heading_anchors_cache:
        return _heading_anchors_cache[slug]
    path = SIGMA_DOCS / f"{slug}.html"
    result: dict[str, str] = {}
    if path.exists():
        html = path.read_text(encoding="utf-8")
        for m in HTML_HEADING_RE.finditer(html):
            anchor = m.group("anchor")
            text = _normalize_heading(m.group("inner"))
            if text and text not in result:
                result[text] = anchor
    _heading_anchors_cache[slug] = result
    return result


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


def clean_qmd(body: str) -> str:
    body = CALLOUT_OPEN_RE.sub("", body)
    body = FENCED_DIV_RE.sub("", body)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text) if len(t) >= 3]


_chunk_index_cache = {"chunks": None, "chapters": None}


def _load_chapter_raw(slug: str):
    for d in CHAPTERS_DIRS:
        p = d / f"{slug}.qmd"
        if p.exists():
            raw = p.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(raw)
            title = meta.get("title") or slug.replace("_", " ")
            return title, clean_qmd(body)
    return None


def build_chunk_index(force: bool = False):
    if not force and _chunk_index_cache["chunks"] is not None:
        return _chunk_index_cache["chunks"], _chunk_index_cache["chapters"]
    chunks, chapters_meta, seen = [], [], set()
    heading_pat = re.compile(r"^(#{2,4})\s+(.+?)(\s*\{[^}]*\})?\s*$")
    for d in CHAPTERS_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.qmd")):
            slug = p.stem
            if slug in SKIP_SLUGS or slug in seen:
                continue
            seen.add(slug)
            raw = p.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(raw)
            title = meta.get("title") or slug.replace("_", " ")
            body = clean_qmd(body)
            anchors = load_heading_anchors(slug)
            chapters_meta.append({"slug": slug, "title": title, "path": str(p)})
            current_section, current_anchor = "", ""
            for idx, para in enumerate(re.split(r"\n\s*\n", body)):
                stripped = para.strip()
                h = heading_pat.match(stripped) if stripped else None
                if h:
                    current_section = h.group(2).strip()
                    current_anchor = anchors.get(_normalize_heading(current_section), "")
                    continue
                text = re.sub(r"\s+", " ", para).strip()
                if len(text) < 80:
                    continue
                chunks.append({
                    "slug": slug, "title": title, "idx": idx,
                    "text": text, "tokens": set(_tokenize(text)),
                    "section": current_section, "anchor": current_anchor,
                })
    _chunk_index_cache.update(chunks=chunks, chapters=chapters_meta)
    return chunks, chapters_meta


# ---------------------------------------------------------------------------
# Structural index (definitions, theorems, etc.) — pre-built by build_structural_index.py
# ---------------------------------------------------------------------------

_structural_cache = {"data": None, "mtime": 0.0}


def load_structural_index():
    if not STRUCTURAL_INDEX_PATH.exists():
        return None
    mtime = STRUCTURAL_INDEX_PATH.stat().st_mtime
    if _structural_cache["data"] is None or mtime != _structural_cache["mtime"]:
        _structural_cache["data"] = json.loads(STRUCTURAL_INDEX_PATH.read_text(encoding="utf-8"))
        _structural_cache["mtime"] = mtime
    return _structural_cache["data"]


# ---------------------------------------------------------------------------
# Tools (called via /api/textbook/*)
# ---------------------------------------------------------------------------

def chapter_url(slug: str, anchor: str = "") -> str:
    """Canonical URL the browser-side agent must use when linking to a chapter.

    If `anchor` is given, returns `/slug.html#anchor` — a deep link to the
    specific Quarto-generated section heading.
    """
    base = f"/{slug}.html"
    return f"{base}#{anchor}" if anchor else base


def _with_url(item: dict) -> dict:
    """Add canonical url to any item that carries a slug (+ optional anchor)."""
    slug = item.get("slug") or item.get("chapter_slug")
    if slug and "url" not in item:
        item = {**item, "url": chapter_url(slug, item.get("anchor", ""))}
    return item


def tool_search(query: str, top_k: int = 5, exclude_slug: str | None = None):
    chunks, _ = build_chunk_index()
    qset = set(_tokenize(query))
    if not qset:
        return []
    scored = []
    for c in chunks:
        if exclude_slug and c["slug"] == exclude_slug:
            continue
        hits = qset & c["tokens"]
        if not hits:
            continue
        bonus = sum(c["text"].lower().count(t) * 0.05 for t in hits)
        scored.append((len(hits) + bonus, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for _, c in scored[:top_k]:
        snippet = c["text"]
        if len(snippet) > 500:
            snippet = snippet[:500] + "…"
        item = {
            "slug": c["slug"],
            "title": c["title"],
            "url": chapter_url(c["slug"], c.get("anchor", "")),
            "snippet": snippet,
        }
        if c.get("section"):
            item["section"] = c["section"]
        out.append(item)
    return out


def tool_read(slug: str, section: str | None = None, max_chars: int = 8000):
    loaded = _load_chapter_raw(slug)
    if not loaded:
        return None
    title, body = loaded
    anchors = load_heading_anchors(slug)
    if section:
        # Find section by case-insensitive title match in headings.
        secs = list(SECTION_RE.finditer(body))
        for i, m in enumerate(secs):
            head = m.group(2).strip()
            if section.lower() in head.lower():
                start = m.end()
                end = secs[i + 1].start() if i + 1 < len(secs) else len(body)
                excerpt = body[start:end].strip()
                if len(excerpt) > max_chars:
                    excerpt = excerpt[:max_chars] + "\n\n[…секция обрезана]"
                anchor = anchors.get(_normalize_heading(head), "")
                return {"slug": slug, "title": title,
                        "url": chapter_url(slug, anchor),
                        "section": head, "text": excerpt}
        return {"slug": slug, "title": title, "url": chapter_url(slug),
                "section": section, "text": "", "error": "section not found"}
    truncated = False
    if len(body) > max_chars:
        body = body[:max_chars] + "\n\n[…глава обрезана]"
        truncated = True
    return {"slug": slug, "title": title, "url": chapter_url(slug),
            "text": body, "truncated": truncated}


def tool_outline():
    idx = load_structural_index()
    if idx:
        return [{
            "slug": c["slug"], "title": c["title"], "url": chapter_url(c["slug"]),
            "outline": c.get("outline", []),
        } for c in idx["chapters"]]
    _, chapters = build_chunk_index()
    return [{"slug": c["slug"], "title": c["title"], "url": chapter_url(c["slug"]),
             "outline": []} for c in chapters]


def tool_find_definition(term: str):
    idx = load_structural_index()
    if not idx:
        return []
    term_l = term.lower()
    matches = []
    for d in idx.get("definitions", []):
        score = 0
        if term_l in d["title"].lower():
            score += 10
        if term_l in d["text"].lower():
            score += 1
        if score > 0:
            matches.append((score, d))
    matches.sort(key=lambda x: x[0], reverse=True)
    return [_with_url(m[1]) for m in matches[:5]]


def tool_find_theorem(query: str):
    idx = load_structural_index()
    if not idx:
        return []
    q_l = query.lower()
    pool = idx.get("theorems", []) + idx.get("lemmas", []) + idx.get("corollaries", [])
    matches = []
    for t in pool:
        score = 0
        if q_l in t["title"].lower():
            score += 10
        if q_l in t["text"].lower():
            score += 1
        if score > 0:
            matches.append((score, t))
    matches.sort(key=lambda x: x[0], reverse=True)
    return [_with_url(m[1]) for m in matches[:5]]


# ---------------------------------------------------------------------------
# OpenRouter streaming proxy
# ---------------------------------------------------------------------------

ALLOWED_FORWARD_FIELDS = {
    "model", "messages", "tools", "tool_choice",
    "temperature", "top_p", "max_tokens", "stream",
    "stop", "response_format",
}


def _messages_have_images(messages) -> bool:
    """Detect OpenAI-style multimodal content (image_url) anywhere in messages."""
    if not isinstance(messages, list):
        return False
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
    return False


USAGE_LOG_PATH = Path(__file__).resolve().parent / "eval" / "usage_log.jsonl"


def _log_usage(model_id: str, raw: bytes) -> None:
    """Parse the final `usage` object (with real OpenRouter cost) out of a
    streamed SSE response and append it to USAGE_LOG_PATH. Best-effort: any
    error is swallowed so cost-logging never affects the proxied response."""
    usage = None
    for frame in raw.decode("utf-8", "replace").split("\n\n"):
        for line in frame.splitlines():
            if not line.startswith("data:"):
                continue
            p = line[5:].strip()
            if p == "[DONE]":
                continue
            try:
                d = json.loads(p)
            except Exception:
                continue
            if isinstance(d, dict) and isinstance(d.get("usage"), dict):
                usage = d["usage"]
    if not usage:
        return
    rec = {
        "ts": time.time(),
        "model": model_id,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "cost": usage.get("cost"),  # OpenRouter actual $ charged, if provided
    }
    try:
        with open(USAGE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _build_openrouter_payload(client_body: dict, skip_models: tuple = ()) -> dict:
    out = {k: v for k, v in client_body.items() if k in ALLOWED_FORWARD_FIELDS}
    if not out.get("model"):
        # Paid override wins: единая стабильная модель, без free-ranker карусели.
        if PAID_MODEL:
            out["model"] = (
                PAID_VISION_MODEL if _messages_have_images(out.get("messages"))
                else PAID_MODEL
            )
        elif _messages_have_images(out.get("messages")):
            _refresh_models()
            now = time.time()
            chain = _model_cache.get("vision_chain") or []
            chosen = next(
                (m for m in chain
                 if m not in skip_models and now >= _bad_models.get(m, 0.0)),
                None,
            )
            out["model"] = chosen or fetch_top_model(vision=True)
        else:
            out["model"] = fetch_top_model(vision=False)
    out.setdefault("stream", True)
    if out.get("stream"):
        # Ask OpenRouter to emit a final usage chunk (with real cost) for the bench.
        out["usage"] = {"include": True}
    return out


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # --- GET ---
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/model":
            q = urllib.parse.parse_qs(parsed.query)
            vision = (q.get("vision", ["0"])[0] or "").lower() in ("1", "true", "yes")
            text_m = PAID_MODEL or fetch_top_model(vision=False)
            vis_m = PAID_VISION_MODEL or fetch_top_model(vision=True)
            return self._send_json({
                "model": vis_m if vision else text_m,
                "vision": vision,
                "text_model": text_m,
                "vision_model": vis_m,
            })
        if parsed.path == "/api/textbook/outline":
            return self._send_json({"chapters": tool_outline()})
        if parsed.path == "/healthz":
            chunks, _ = build_chunk_index()
            idx = load_structural_index() or {}
            return self._send_json({
                "ok": True,
                "chunks": len(chunks),
                "definitions": len(idx.get("definitions", [])),
                "theorems": len(idx.get("theorems", [])),
                "model": fetch_top_model(),
                "vision_model": fetch_top_model(vision=True),
            })
        return self._send_json({"error": "not found"}, status=404)

    # --- POST ---
    def do_POST(self):
        try:
            body = self._read_json()
        except Exception as e:
            return self._send_json({"error": f"bad request: {e}"}, status=400)

        if self.path == "/api/textbook/search":
            q = (body.get("query") or "").strip()
            top_k = int(body.get("top_k") or 5)
            exclude = body.get("exclude_slug") or None
            return self._send_json({"results": tool_search(q, top_k=top_k, exclude_slug=exclude)})

        if self.path == "/api/textbook/read":
            slug = (body.get("slug") or "").strip()
            section = (body.get("section") or "").strip() or None
            res = tool_read(slug, section=section)
            if not res:
                return self._send_json({"error": f"chapter {slug!r} not found"}, status=404)
            return self._send_json(res)

        if self.path == "/api/textbook/find_definition":
            term = (body.get("term") or "").strip()
            return self._send_json({"results": tool_find_definition(term)})

        if self.path == "/api/textbook/find_theorem":
            q = (body.get("query") or "").strip()
            return self._send_json({"results": tool_find_theorem(q)})

        if self.path == "/api/llm":
            model = (body.get("model") or PAID_MODEL or "").strip()
            if _is_gigachat_model(model):
                if not GIGACHAT_AUTH_KEY:
                    return self._send_json({"error": "GIGACHAT_AUTH_KEY missing"}, status=500)
                return self._proxy_gigachat(body)
            if not API_KEY:
                return self._send_json({"error": "OPENROUTER_API_KEY missing"}, status=500)
            return self._proxy_openrouter(body)

        return self._send_json({"error": "not found"}, status=404)

    def _proxy_gigachat(self, client_body):
        """Route /api/llm to GigaChat with STREAMING pass-through + per-frame
        translation into the OpenAI SSE shape the browser agent understands.

        Streaming (not buffer-then-emit) is REQUIRED for correct measurement:
        assistant.js aborts a completion after LLM_IDLE_MS=120s with NO bytes, so
        a non-streaming upstream made long answers (esp. GigaChat-2-Max) falsely
        DNF. Streaming keeps bytes flowing so only a genuine model failure scores
        0. GigaChat streams `function_call` whole in one delta and carries `usage`
        (with token counts) in its final frame → real ₽→$ cost is logged.

        On a transient upstream failure BEFORE any byte is forwarded, retry up to
        2× (fresh token on auth issues) so throttling/5xx doesn't poison a case."""
        payload = build_gigachat_payload(client_body)
        payload["stream"] = True
        model_id = payload["model"]
        print(f"[proxy-giga] model={model_id} msgs={len(payload['messages'])} "
              f"fn={'functions' in payload}", file=sys.stderr)

        upstream = None
        for attempt in range(3):
            try:
                token = get_gigachat_token()
                req = urllib.request.Request(
                    GIGACHAT_CHAT_URL,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Authorization": f"Bearer {token}",
                             "Content-Type": "application/json",
                             "Accept": "text/event-stream"},
                    method="POST",
                )
                upstream = urllib.request.urlopen(req, context=_GIGA_SSL, timeout=180)
                break
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", "replace")[:300]
                print(f"[proxy-giga] HTTP {e.code} attempt {attempt}: {err_body}", file=sys.stderr)
                if e.code == 401:
                    _giga_token["tok"] = None  # force token refresh next attempt
                if attempt == 2:
                    return self._send_json({"error": f"gigachat HTTP {e.code}: {err_body}"}, status=502)
                time.sleep(1.5 * (attempt + 1))
            except Exception as e:
                print(f"[proxy-giga] open failed attempt {attempt}: {e}", file=sys.stderr)
                if attempt == 2:
                    return self._send_json({"error": f"gigachat: {e}"}, status=502)
                time.sleep(1.5 * (attempt + 1))
        if upstream is None:
            return self._send_json({"error": "gigachat unavailable"}, status=502)

        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        usage = None
        buf = b""
        call_id = f"call_giga_{uuid.uuid4().hex[:8]}"

        def emit(obj):
            self.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()

        try:
            while True:
                chunk = upstream.read1(4096) if hasattr(upstream, "read1") else upstream.read(4096)
                if not chunk:
                    break
                buf += chunk
                text = buf.decode("utf-8", "replace")
                frames = text.split("\n\n")
                buf = frames[-1].encode("utf-8")  # keep incomplete tail
                for frame in frames[:-1]:
                    for line in frame.splitlines():
                        if not line.startswith("data:"):
                            continue
                        p = line[5:].strip()
                        if p == "[DONE]":
                            continue
                        try:
                            g = json.loads(p)
                        except Exception:
                            continue
                        if isinstance(g.get("usage"), dict):
                            usage = g["usage"]
                        ch = (g.get("choices") or [{}])[0]
                        delta = ch.get("delta") or {}
                        fin = ch.get("finish_reason")
                        fc = delta.get("function_call")
                        if fc:
                            args = fc.get("arguments")
                            if not isinstance(args, str):
                                args = json.dumps(args, ensure_ascii=False)
                            out_delta = {"role": "assistant", "tool_calls": [{
                                "index": 0, "id": call_id, "type": "function",
                                "function": {"name": fc.get("name", ""), "arguments": args},
                            }]}
                        elif delta.get("content") is not None:
                            out_delta = {"content": delta.get("content")}
                        else:
                            out_delta = {}
                        out_fin = "tool_calls" if fin == "function_call" else fin
                        out = {"choices": [{"delta": out_delta, "index": 0}],
                               "model": model_id, "object": "chat.completion.chunk"}
                        if out_fin:
                            out["choices"][0]["finish_reason"] = out_fin
                        if out_delta or out_fin:
                            emit(out)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            print(f"[proxy-giga] client gone mid-stream: {e}", file=sys.stderr)
        finally:
            upstream.close()
            if usage:
                tot = usage.get("total_tokens") or 0
                rate = GIGACHAT_PRICE_RUB_PER_1K.get(model_id, 0.065)
                cost = round(tot / 1000.0 * rate / GIGACHAT_RUB_PER_USD, 6)
                try:
                    with open(USAGE_LOG_PATH, "a", encoding="utf-8") as f:
                        f.write(json.dumps({
                            "ts": time.time(), "model": model_id,
                            "prompt_tokens": usage.get("prompt_tokens"),
                            "completion_tokens": usage.get("completion_tokens"),
                            "total_tokens": tot, "cost": cost,
                        }, ensure_ascii=False) + "\n")
                except Exception:
                    pass

    def _open_upstream(self, payload, wants_stream):
        req = urllib.request.Request(
            OPENROUTER_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://sigma.fmin.xyz",
                "X-Title": "Sigma Assistant",
                "Accept": "text/event-stream" if wants_stream else "application/json",
            },
            method="POST",
        )
        return urllib.request.urlopen(req, timeout=180)

    def _peek_sse(self, upstream, deadline_s: float = 45.0):
        """Read SSE frames into a buffer until the first `data:` frame.

        Returns (buffered_bytes, first_data_json_or_None, eof_bool). Heartbeats
        (`: ...`) are buffered but don't count as the first frame. If the first
        data frame carries an `error`, the caller can retry with another model.
        """
        buf = b""
        deadline = time.time() + deadline_s
        while time.time() < deadline:
            chunk = upstream.read1(4096) if hasattr(upstream, "read1") else upstream.read(4096)
            if not chunk:
                return buf, None, True
            buf += chunk
            # Find complete frames separated by \n\n
            text = buf.decode("utf-8", "replace")
            frames = text.split("\n\n")
            for frame in frames[:-1]:
                for line in frame.splitlines():
                    if line.startswith("data:"):
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            return buf, None, True
                        try:
                            return buf, json.loads(payload), False
                        except Exception:
                            pass
        return buf, None, False  # timeout: pass-through whatever we got

    def _proxy_openrouter(self, client_body):
        wants_stream = bool(client_body.get("stream", True))
        tried: list[str] = []
        upstream = None
        buffered = b""
        has_img = _messages_have_images(client_body.get("messages"))
        print(f"[proxy] new request: vision={has_img} msgs={len(client_body.get('messages') or [])}", file=sys.stderr)
        # Walk the entire vision chain on transient errors; ~5 covers shir-man
        # top + main catalogue free models.
        max_attempts = 5
        for attempt in range(max_attempts):
            payload = _build_openrouter_payload(client_body, skip_models=tuple(tried))
            model_id = payload["model"]
            tried.append(model_id)
            try:
                upstream = self._open_upstream(payload, wants_stream)
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                # 429/502/503/504 → upstream down or rate-limited; rotate if vision.
                if e.code in (429, 502, 503, 504) and attempt < max_attempts - 1 and _messages_have_images(payload.get("messages")):
                    mark_model_bad(model_id)
                    continue
                return self._send_json({"error": f"HTTP {e.code}: {body[:500]}"}, status=502)
            except Exception as e:
                return self._send_json({"error": str(e)}, status=502)

            if not wants_stream:
                data = upstream.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return

            # Peek for early provider error before committing to pass-through.
            buffered, first_frame, eof = self._peek_sse(upstream, deadline_s=30.0)
            error = (first_frame or {}).get("error") if first_frame else None
            stalled = first_frame is None and not eof  # heartbeats only, no data
            if (error or stalled) and attempt < max_attempts - 1 and _messages_have_images(payload.get("messages")):
                upstream.close()
                reason = "error " + str(error.get("code") or "") if error else "stalled (heartbeats only)"
                print(f"[proxy] {model_id} {reason}; rotating", file=sys.stderr)
                mark_model_bad(model_id)
                upstream = None
                continue
            break

        if upstream is None:
            return self._send_json({"error": "all vision models unavailable"}, status=502)

        # Commit headers and replay buffered bytes, then pass-through the tail.
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        cap = bytearray(buffered)  # tee the stream to recover the usage/cost chunk
        try:
            if buffered:
                self.wfile.write(buffered)
                self.wfile.flush()
            while True:
                chunk = upstream.read1(4096) if hasattr(upstream, "read1") else upstream.read(4096)
                if not chunk:
                    break
                cap.extend(chunk)
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break
        finally:
            upstream.close()
            _log_usage(model_id, bytes(cap))


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8766))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"sigma-assistant on http://{host}:{port}")
    print(f"OPENROUTER_API_KEY: {'set' if API_KEY else 'MISSING'}")
    chunks, chapters = build_chunk_index()
    idx = load_structural_index() or {}
    print(f"Chunks: {len(chunks)}  Chapters: {len(chapters)}")
    print(f"Definitions: {len(idx.get('definitions', []))}  Theorems: {len(idx.get('theorems', []))}")
    ThreadingServer((host, port), Handler).serve_forever()
