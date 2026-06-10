#!/usr/bin/env python3
"""Offline validation of the NEW tool_search ranking (IDF + coverage +
proper-noun boost + ru_stem + section-heading tokens) against the real chunk
index, on the exact failing queries. Compares OLD vs NEW ranking without
touching the live server."""
import math, re
from collections import Counter
import server  # module-level safe (HTTP server only under __main__)
from ru_stem import stem

chunks, _ = server.build_chunk_index()
TOKEN_RE = server.TOKEN_RE

def toks(s):
    return [t.lower() for t in TOKEN_RE.findall(s) if len(t) >= 3]

import os
USE_SECTION = os.environ.get("SEC", "1") == "1"
# augment chunks: stems (+ optional section tokens)
for c in chunks:
    base_tokens = set(c["tokens"])
    if USE_SECTION:
        base_tokens |= set(toks(c.get("section", "")))
    c["_tokens2"] = base_tokens
    c["_stems"] = set(stem(t) for t in c["_tokens2"])

N = len(chunks)
df = Counter()
for c in chunks:
    for s in c["_stems"]:
        df[s] += 1
idf = lambda s: math.log((N + 1) / (df.get(s, 0) + 1)) + 1

def old_search(query, k=5, exclude=None):
    qset = set(toks(query))
    scored = []
    for c in chunks:
        if exclude and c["slug"] == exclude: continue
        hits = qset & set(c["tokens"])
        if not hits: continue
        bonus = sum(c["text"].lower().count(t) * 0.05 for t in hits)
        scored.append((len(hits) + bonus, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:k]

def new_search(query, k=6, exclude=None):
    qtokens = toks(query)
    # proper-noun set: original query words that are capitalized or long
    qraw = [w for w in TOKEN_RE.findall(query) if len(w) >= 3]
    proper_stems = set(stem(w.lower()) for w in qraw if (w[:1].isupper() or len(w) >= 7))
    qstems = set(stem(t) for t in qtokens)
    scored = []
    for c in chunks:
        if exclude and c["slug"] == exclude: continue
        hits = qstems & c["_stems"]
        if not hits: continue
        base = sum(idf(t) for t in hits)
        boost = sum(3.0 * idf(t) for t in hits if t in proper_stems)
        coverage = (len(hits) / max(1, len(qstems))) ** 0.5
        scored.append((coverage * (base + boost), c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:k]

TESTS = [
    ("Канторович Нобелевская премия", "1975", "оптимал"),
    ("Канторович", "1975", "оптимал"),
    ("кто придумал RSA", "1978", None),
    ("RSA", "1978", None),
    ("сходимость метода Герона", "квадратичн", None),
]

def chunk_has(c, *needles):
    t = c["text"].lower()
    return all(n.lower() in t for n in needles if n)

for query, *needles in TESTS:
    needles = [n for n in needles if n]
    print(f"\n=== QUERY: «{query}»  (нужен chunk с {needles}) ===")
    for label, res in (("OLD", old_search(query)), ("NEW", new_search(query))):
        rank_hit = None
        for i, (sc, c) in enumerate(res, 1):
            if chunk_has(c, *needles):
                rank_hit = i; break
        top = res[0][1] if res else None
        print(f"  {label}: target_chunk rank = {rank_hit if rank_hit else 'НЕ В ТОПЕ'}"
              f" | top1 = {top['slug'] if top else '-'} / «{(top['text'][:55] if top else '')}…»")
