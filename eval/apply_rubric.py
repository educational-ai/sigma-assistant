#!/usr/bin/env python3
"""Apply rubric-workflow verdicts and report binary-vs-continuous scoring.

The rubric grading stack (rubrics.jsonl + rubric_score.py + grade_rubric.py) was
fully built and unit-tested but never RUN: rubric_verdicts.jsonl was empty, so
the live board ran on the cruder binary judge (grade_hybrid). This wires the last
mile in one command:

  1. snapshot each model's CURRENT binary pass_rate from bench.json
     (before grade_rubric.py overwrites `pass_rate` with the rubric-threshold view);
  2. persist the workflow's finalVerdicts → rubric_verdicts.jsonl;
  3. run grade_rubric.py (writes avg_rubric_score + rubric pass_rate into bench.json);
  4. print a comparison table: binary clean-pass % vs continuous rubric %, sorted,
     flagging the biggest disagreements (where the rubric is more discriminating).

It does NOT touch the live page — gen_benchmark_page.py + deploy stays a propose.

Usage:
  python3 apply_rubric.py finalVerdicts.json        # full pipeline + report
  python3 apply_rubric.py --report-only             # just re-print the comparison
"""
import json, subprocess, sys
from pathlib import Path

EVAL = Path(__file__).resolve().parent


def short(model):
    return model.split("/")[-1]


def snapshot_binary():
    """Per-model binary clean-pass rate from the CURRENT bench.json (pre-rubric)."""
    snap = {}
    for bj in sorted((EVAL / "bench").glob("*/bench.json")):
        b = json.loads(bj.read_text(encoding="utf-8"))
        n = len(b["cases"]) or 1
        clean = sum(1 for c in b["cases"] if c.get("pass"))
        snap[short(b["model"])] = {"rate": clean / n, "clean": clean, "n": n}
    return snap


def read_rubric():
    """Per-model continuous rubric score + rubric-threshold pass from bench.json."""
    out = {}
    for bj in sorted((EVAL / "bench").glob("*/bench.json")):
        b = json.loads(bj.read_text(encoding="utf-8"))
        n = len(b["cases"]) or 1
        out[short(b["model"])] = {
            "avg": b.get("avg_rubric_score"),
            "passed": b.get("passed"),
            "n": n,
        }
    return out


def report(binary):
    rub = read_rubric()
    rows = []
    for m, rb in rub.items():
        bn = binary.get(m, {})
        b_rate = bn.get("rate")
        avg = rb.get("avg")
        if avg is None:
            continue
        disagree = abs((b_rate if b_rate is not None else 0) - avg)
        rows.append((m, b_rate, avg, rb.get("passed"), rb.get("n"), disagree))
    # sort by continuous rubric score desc
    rows.sort(key=lambda r: -(r[2] if r[2] is not None else -1))

    print(f"{'model':36} {'binary%':>8} {'rubric%':>8} {'Δ':>6}")
    print("-" * 62)
    for m, b_rate, avg, passed, n, d in rows:
        bs = f"{b_rate*100:6.1f}" if b_rate is not None else "   —  "
        print(f"{m:36} {bs:>8} {avg*100:7.1f} {d*100:6.1f}")

    print()
    biggest = sorted(rows, key=lambda r: -r[5])[:5]
    print("Крупнейшие расхождения binary↔rubric (рубрика дискриминативнее):")
    for m, b_rate, avg, passed, n, d in biggest:
        bs = f"{b_rate*100:.0f}%" if b_rate is not None else "—"
        print(f"  {m:36} binary {bs:>4} → rubric {avg*100:.0f}%  (Δ {d*100:.0f}pp)")


def main():
    args = sys.argv[1:]
    if args and args[0] == "--report-only":
        report(snapshot_binary())
        return
    if not args:
        print("usage: apply_rubric.py finalVerdicts.json | --report-only")
        sys.exit(1)

    binary = snapshot_binary()  # BEFORE grade_rubric overwrites pass_rate

    p = subprocess.run(["python3", str(EVAL / "persist_rubric_verdicts.py"), args[0]],
                       capture_output=True, text=True)
    print(p.stdout.strip());
    if p.returncode != 0:
        print(p.stderr); sys.exit(1)

    g = subprocess.run(["python3", str(EVAL / "grade_rubric.py")],
                       capture_output=True, text=True)
    print(g.stdout.strip())
    if g.returncode != 0:
        print(g.stderr); sys.exit(1)

    print("\n" + "=" * 62)
    report(binary)


if __name__ == "__main__":
    main()
