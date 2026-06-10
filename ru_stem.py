#!/usr/bin/env python3
"""Dependency-free Russian Snowball (Porter) stemmer. Pure stdlib.

Used by tool_search to make keyword matching morphology-insensitive:
"Нобелевскую"/"Нобелевской"/"Нобелевская" all stem to the same root, so a
student's inflected query matches the book's differently-inflected text.

Standard Snowball algorithm (snowballstem.org/algorithms/russian).
"""
import re

VOWELS = "аеиоуыэюя"

PERFECTIVE_GERUND_1 = ("в", "вши", "вшись")
PERFECTIVE_GERUND_2 = ("ив", "ивши", "ившись", "ыв", "ывши", "ывшись")
ADJECTIVE = ("ее", "ие", "ые", "ое", "ими", "ыми", "ей", "ий", "ый", "ой",
             "ем", "им", "ым", "ом", "его", "ого", "ему", "ому", "их", "ых",
             "ую", "юю", "ая", "яя", "ою", "ею")
PARTICIPLE_1 = ("ем", "нн", "вш", "ющ", "щ")
PARTICIPLE_2 = ("ивш", "ывш", "ующ")
REFLEXIVE = ("ся", "сь")
VERB_1 = ("ла", "на", "ете", "йте", "ли", "й", "л", "ем", "н", "ло", "но",
          "ет", "ют", "ны", "ть", "ешь", "нно")
VERB_2 = ("ила", "ыла", "ена", "ейте", "уйте", "ите", "или", "ыли", "ей",
          "уй", "ил", "ыл", "им", "ым", "ен", "ило", "ыло", "ено", "ят",
          "ует", "уют", "ит", "ыт", "ены", "ить", "ыть", "ишь", "ую", "ю")
NOUN = ("а", "ев", "ов", "ие", "ье", "е", "иями", "ями", "ами", "еи", "ии",
        "и", "ией", "ей", "ой", "ий", "й", "иям", "ям", "ием", "ем", "ам",
        "ом", "о", "у", "ах", "иях", "ях", "ы", "ь", "ию", "ью", "ю", "ия",
        "ья", "я")
SUPERLATIVE = ("ейш", "ейше")
DERIVATIONAL = ("ост", "ость")


def _rv(word):
    # RV = region after the first vowel
    for i, ch in enumerate(word):
        if ch in VOWELS:
            return i + 1
    return len(word)


def _r2(word):
    # R1 = after first vowel followed by non-vowel; R2 = same within R1
    def region(s):
        for i in range(len(s) - 1):
            if s[i] in VOWELS and s[i + 1] not in VOWELS:
                return i + 2
        return len(s)
    r1 = region(word)
    r2 = r1 + region(word[r1:])
    return r2


def _ends_any(s, suffixes):
    # longest matching suffix
    best = None
    for suf in suffixes:
        if s.endswith(suf) and (best is None or len(suf) > len(best)):
            best = suf
    return best


def stem(word):
    word = word.lower().replace("ё", "е")
    if not word:
        return word
    rv = _rv(word)
    pre, rvp = word[:rv], word[rv:]

    # Step 1
    g = _ends_any(rvp, PERFECTIVE_GERUND_2)
    if g:
        rvp = rvp[: -len(g)]
    else:
        g = _ends_any(rvp, PERFECTIVE_GERUND_1)
        # group-1 gerunds must follow а/я
        if g and rvp[: -len(g)].endswith(("а", "я")):
            rvp = rvp[: -len(g)]
        else:
            # reflexive
            r = _ends_any(rvp, REFLEXIVE)
            if r:
                rvp = rvp[: -len(r)]
            # adjectival = adjective (+ optional participle)
            a = _ends_any(rvp, ADJECTIVE)
            if a:
                rvp = rvp[: -len(a)]
                p = _ends_any(rvp, PARTICIPLE_2)
                if p:
                    rvp = rvp[: -len(p)]
                else:
                    p = _ends_any(rvp, PARTICIPLE_1)
                    if p and rvp[: -len(p)].endswith(("а", "я")):
                        rvp = rvp[: -len(p)]
            else:
                v = _ends_any(rvp, VERB_2)
                if v:
                    rvp = rvp[: -len(v)]
                else:
                    v = _ends_any(rvp, VERB_1)
                    if v and rvp[: -len(v)].endswith(("а", "я")):
                        rvp = rvp[: -len(v)]
                    else:
                        n = _ends_any(rvp, NOUN)
                        if n:
                            rvp = rvp[: -len(n)]

    word = pre + rvp
    rv = _rv(word)
    pre, rvp = word[:rv], word[rv:]

    # Step 2: remove и
    if rvp.endswith("и"):
        rvp = rvp[:-1]

    # Step 3: derivational (ость/ость in R2)
    r2 = _r2(word)
    d = _ends_any(rvp, DERIVATIONAL)
    if d and len(pre) + (len(rvp) - len(d)) >= r2 - len(pre) and (pre + rvp).rfind(d) >= r2:
        rvp = rvp[: -len(d)]

    # Step 4: superlative + double н + soft sign
    if rvp.endswith("нн"):
        rvp = rvp[:-1]
    else:
        s = _ends_any(rvp, SUPERLATIVE)
        if s:
            rvp = rvp[: -len(s)]
            if rvp.endswith("нн"):
                rvp = rvp[:-1]
    if rvp.endswith("ь"):
        rvp = rvp[:-1]

    return pre + rvp


if __name__ == "__main__":
    groups = [
        ["Нобелевскую", "Нобелевской", "Нобелевская", "нобелевские"],
        ["премию", "премии", "премия", "премий"],
        ["сходимость", "сходимости", "сходится", "сходимостью"],
        ["оптимальный", "оптимального", "оптимальное", "оптимальных", "оптимальном"],
        ["получил", "получила", "получили", "получить"],
        ["Канторович", "Канторовича", "Канторовичем"],
        ["квадратичная", "квадратичной", "квадратичную", "квадратично"],
        ["производная", "производной", "производную"],
        ["выпуклая", "выпуклой", "выпуклую", "выпуклости"],
        ["придумал", "придумали", "придумать"],
    ]
    all_ok = True
    for g in groups:
        stems = [stem(w) for w in g]
        ok = len(set(stems)) == 1
        all_ok &= ok
        print(("OK " if ok else "XX "), g[0], "→", stems)
    print("\nALL CONVERGE:", all_ok)
