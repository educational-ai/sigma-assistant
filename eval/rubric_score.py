#!/usr/bin/env python3
"""Deterministic rubric aggregation for the Sigma benchmark.

The Claude judge grades each answer per CRITERION (met / partial / none) instead
of a single binary pass/fail. This module turns those per-criterion levels into a
weighted 0..1 score — arithmetic stays in Python (never trust an LLM to sum).

Scoring:
  level value: met=1.0, partial=0.5, none=0.0
  score = Σ(weight_i · value_i) / Σ(weight_i)
  CRITICAL gate: if any criterion marked `critical` is `none`, the answer hard-
  fails → score 0.0 (preserves "галлюцинация/ложное подтверждение = незачёт").
  A `partial` on a critical criterion does NOT zero the score — it just
  contributes 0.5·weight like any other.

A binary `pass` is still derived from a threshold (PASS_THRESHOLD) for backward
compatibility with fields/consumers that expect it, but the headline metric is
the continuous rubric score.
"""
import json
from pathlib import Path

EVAL = Path(__file__).resolve().parent
RUBRICS_PATH = EVAL / "rubrics.jsonl"

LEVEL_VALUE = {"met": 1.0, "partial": 0.5, "none": 0.0}
PASS_THRESHOLD = 0.6  # rubric_score ≥ this ⇒ pass (for the optional binary view)


def load_rubrics():
    out = {}
    if not RUBRICS_PATH.exists():
        return out
    for line in RUBRICS_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["case_id"]] = r["criteria"]
    return out


RUBRICS = load_rubrics()


SHORT_MAX_CHARS = 150  # greeting "short" threshold


def _auto_level(kind, obs):
    """Objective criterion level computed from run data (not the LLM judge).
    obs: {tool_match: bool, n_tools: int, images: int, answer_len: int, n_python: int}."""
    obs = obs or {}
    if kind == "tool_expected":
        return "met" if obs.get("tool_match") else "none"
    if kind == "no_tools":
        return "met" if int(obs.get("n_tools", 0) or 0) == 0 else "none"
    if kind == "image_present":
        return "met" if int(obs.get("images", 0) or 0) > 0 else "none"
    if kind == "short":
        return "met" if 0 < int(obs.get("answer_len", 0) or 0) <= SHORT_MAX_CHARS else "none"
    if kind == "min2_python":
        return "met" if int(obs.get("n_python", 0) or 0) >= 2 else "none"
    return "none"


def score_answer(case_id, levels, obs=None):
    """levels: {criterion_id: 'met'|'partial'|'none'} — judged criteria only.
    obs: run data for `auto` criteria (tool_match / n_tools / images).
    Returns dict: {score, passed, capped, per_criterion:[...], missing:[ids]}.
    Unknown/absent JUDGED criteria default to 'none'. Returns None if no rubric."""
    criteria = RUBRICS.get(case_id)
    if not criteria:
        return None
    total_w = sum(c["weight"] for c in criteria) or 1
    earned = 0.0
    capped = False
    per = []
    missing = []
    for c in criteria:
        auto = c.get("auto")
        if auto:
            lvl = _auto_level(auto, obs)
        else:
            lvl = (levels or {}).get(c["id"], "none")
            if c["id"] not in (levels or {}):
                missing.append(c["id"])
        val = LEVEL_VALUE.get(lvl, 0.0)
        earned += c["weight"] * val
        if c.get("critical") and lvl == "none":
            capped = True
        per.append({"id": c["id"], "level": lvl, "weight": c["weight"],
                    "critical": bool(c.get("critical")), "auto": auto or None})
    raw = earned / total_w
    score = 0.0 if capped else round(raw, 4)
    return {
        "score": score,
        "raw_score": round(raw, 4),
        "passed": score >= PASS_THRESHOLD,
        "capped": capped,
        "per_criterion": per,
        "missing": missing,
    }


if __name__ == "__main__":  # tiny self-test
    RUBRICS["_t"] = [
        {"id": "a", "text": "", "weight": 3, "critical": True},
        {"id": "b", "text": "", "weight": 1, "critical": False},
    ]
    print("all met:", score_answer("_t", {"a": "met", "b": "met"}))
    print("crit none:", score_answer("_t", {"a": "none", "b": "met"}))
    print("crit partial:", score_answer("_t", {"a": "partial", "b": "met"}))
