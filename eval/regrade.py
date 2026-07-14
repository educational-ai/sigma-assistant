#!/usr/bin/env python3
"""DEPRECATED shim → grade_hybrid.py.

Голый substring-перескоринг ДВАЖДЫ затирал судейские вердикты семантических
кейсов (инциденты 2026-07-12 и 2026-07-13, аудит major #12): он не знает про
judge_verdicts.jsonl и переписывал judge_pass обратно в substring-провалы.
Единственный канонический перескоринг — grade_hybrid.py (substring для
детерминированных категорий + судейский кэш для семантических + render-гейт).
Этот файл оставлен, чтобы мышечная память «python3 regrade.py» не стреляла в ногу.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
print("regrade.py устарел: единственный перескоринг — grade_hybrid.py (запускаю его)…\n")
import grade_hybrid
grade_hybrid.main()
