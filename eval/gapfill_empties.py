#!/usr/bin/env python3
"""Gap-fill ONLY the empty/no-answer cases of already-benchmarked models.

Rationale (incident 2026-06-10): a blank answer in bench.json was a TIMEOUT
(the eval failed to obtain an answer), scored as a content FAIL. The harness is
now fixed (adaptive timeout in run_eval.py + idle-timeout & forced-final-answer
in assistant.js), so re-running JUST the previously-empty cases per model now
yields real answers. Far cheaper than re-running all 29 cases × N models.

For each model dir under eval/bench/<slug>/ that has ≥1 empty case:
  set_model(model) → restart → run_eval --only <empty ids> → attribute real cost
  → MERGE new case results into the existing bench.json (replace those entries,
    recompute n/passed/answered/no_answer/pass_rate/cost/by_category).

Restores .env verbatim on normal exit AND on SIGTERM/SIGINT (signal handler).

Usage: python3 gapfill_empties.py            # all models with empties
       python3 gapfill_empties.py <slug> ...  # specific bench dirs
"""
import json, os, signal, subprocess, sys, time
from pathlib import Path
EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL.parent)); sys.path.insert(0, str(EVAL))
import bench_models as BM

BENCH = EVAL / "bench"
CASES = EVAL / "cases.jsonl"


def empty_ids(bench):
    return [c["id"] for c in bench["cases"] if not (c.get("answer") or "").strip()]


def gapfill_one(slug, env_backup):
    bj = BENCH / slug / "bench.json"
    if not bj.exists():
        print(f"  !! {slug}: no bench.json", flush=True); return
    bench = json.loads(bj.read_text(encoding="utf-8"))
    ids = empty_ids(bench)
    if not ids:
        print(f"  {slug}: no empties, skip", flush=True); return
    model = bench["model"]
    print(f"\n########## {slug} ({model}) — {len(ids)} empty: {ids} ##########", flush=True)
    BM.set_model(model)
    if not BM.restart_and_wait():
        print(f"  !! service unhealthy after restart, skip {slug}", flush=True); return
    out = EVAL / "reports" / f"_gapfill_{slug}"
    since = time.time()
    subprocess.run([sys.executable, str(EVAL / "run_eval.py"),
                    "--base", BM.BASE, "--out", str(out), "--only", ",".join(ids)],
                   cwd=str(EVAL))
    res_path = out / "results.jsonl"
    if not res_path.exists():
        print(f"  !! {slug}: no results.jsonl", flush=True); return
    results = [json.loads(l) for l in res_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    results = BM.attribute_cost(results, BM.load_usage(since))
    cmeta = {c["id"]: c for c in (json.loads(l) for l in CASES.read_text(encoding="utf-8").splitlines() if l.strip())}
    # index existing cases by id for in-place replacement
    by_id = {c["id"]: c for c in bench["cases"]}
    filled = 0
    for r in results:
        cid = r.get("case_id")
        c = cmeta.get(cid, {})
        new = {
            "id": cid,
            "category": c.get("category", by_id.get(cid, {}).get("category", "?")),
            "question": c.get("question", by_id.get(cid, {}).get("question", "")),
            "pass": r["score"]["pass"],
            "tool_match": r["score"]["tool_match"],
            "answer_match": r["score"]["answer_match"],
            "visual_match": r["score"]["visual_match"],
            "missing": r["score"].get("missing"),
            "tools": [t["tool"] for t in r["obs"]["trace"]],
            "images": r["obs"]["images"],
            "elapsed": round(r.get("elapsed", 0), 1),
            "cost": r.get("cost", 0),
            "prompt_tokens": r.get("prompt_tokens", 0),
            "completion_tokens": r.get("completion_tokens", 0),
            "models_used": r.get("models_used", []),
            "answer": r["obs"]["answer"],
            "no_answer": r["obs"].get("no_answer", False),
        }
        by_id[cid] = new
        if (new["answer"] or "").strip():
            filled += 1
    # rebuild cases in original order
    order = [c["id"] for c in bench["cases"]]
    bench["cases"] = [by_id[i] for i in order]
    _recompute(bench)
    bj.write_text(json.dumps(bench, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  → {slug}: filled {filled}/{len(ids)} | now {bench['passed']}/{bench['n']} pass, "
          f"{bench['no_answer']} no_answer", flush=True)


def _recompute(bench):
    cases = bench["cases"]
    n = len(cases)
    passed = sum(1 for c in cases if c.get("pass"))
    no_ans = sum(1 for c in cases if c.get("no_answer") or not (c.get("answer") or "").strip())
    by_cat = {}
    for c in cases:
        cat = c.get("category", "?")
        by_cat.setdefault(cat, {"pass": 0, "total": 0})
        by_cat[cat]["total"] += 1
        by_cat[cat]["pass"] += 1 if c.get("pass") else 0
    total_cost = round(sum(c.get("cost", 0) for c in cases), 6)
    bench["n"] = n
    bench["passed"] = passed
    bench["answered"] = n - no_ans
    bench["no_answer"] = no_ans
    bench["pass_rate"] = round(passed / n, 4) if n else 0
    bench["total_cost_usd"] = total_cost
    bench["cost_per_q_usd"] = round(total_cost / n, 6) if n else 0
    bench["avg_elapsed_s"] = round(sum(c.get("elapsed", 0) for c in cases) / n, 1) if n else 0
    bench["by_category"] = by_cat


def main():
    env_backup = BM.ENV.read_text(encoding="utf-8")
    (BM.ROOT / ".env.benchbak").write_text(env_backup, encoding="utf-8")
    print(f"backed up .env ({len(env_backup)} bytes)", flush=True)

    def handler(signum, frame):
        try:
            BM.ENV.write_text(env_backup, encoding="utf-8")
            subprocess.run(["pkill", "-f", "run_eval.py"])
            BM.restart_and_wait(timeout=30)
            print(f"\n!! signal {signum} → restored .env + restarted", flush=True)
        finally:
            os._exit(1)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)

    slugs = sys.argv[1:] or sorted(
        d.name for d in BENCH.iterdir()
        if d.is_dir() and (d / "bench.json").exists()
        and empty_ids(json.loads((d / "bench.json").read_text(encoding="utf-8")))
    )
    print(f"gap-filling {len(slugs)} models: {slugs}", flush=True)
    try:
        for slug in slugs:
            try:
                gapfill_one(slug, env_backup)
            except Exception as e:
                print(f"  !! {slug} failed: {e}", flush=True)
    finally:
        BM.ENV.write_text(env_backup, encoding="utf-8")
        BM.restart_and_wait()
        print("restored original .env + restarted", flush=True)
    print("GAPFILL DONE", flush=True)


if __name__ == "__main__":
    main()
