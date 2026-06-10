#!/usr/bin/env python3
"""Phase-2 incremental sweep: run ONLY the new `refuse_unknown_year` case
against each model in bench_models.MODELS and MERGE its record into the
existing eval/bench/<slug>/bench.json — WITHOUT re-running the verified 28.

Per model: set_model -> restart -> run_eval (single-case temp file) ->
attribute real cost from usage_log -> append/replace the case in bench.json.
Idempotent: a model whose bench.json already has refuse_unknown_year is skipped.
Always restores the original .env + restarts at the end (live site back to prod).
"""
import json, os, signal, subprocess, sys, time, tempfile
from pathlib import Path

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL.parent)); sys.path.insert(0, str(EVAL))
import bench_models as BM

NEW_ID = "refuse_unknown_year"
BASE = BM.BASE
ENV = BM.ENV
CASES = BM.CASES  # cases.jsonl (already contains refuse_unknown_year)


def cmeta():
    return {c["id"]: c for c in (json.loads(l) for l in CASES.read_text(encoding="utf-8").splitlines() if l.strip())}


def build_case_record(r, meta):
    c = meta.get(r.get("case_id"), {"id": r.get("case_id"), "category": "?", "question": r.get("case_id", "")})
    s = r["score"]
    rec = {
        "id": c["id"],
        "category": c["category"],
        "question": c["question"],
        "pass": s["pass"],
        "tool_match": s["tool_match"],
        "answer_match": s["answer_match"],
        "visual_match": s["visual_match"],
        "missing": s.get("missing"),
        "tools": [t["tool"] for t in r["obs"]["trace"]],
        "images": r["obs"]["images"],
        "elapsed": round(r.get("elapsed", 0), 1),
        "cost": r.get("cost", 0),
        "prompt_tokens": r.get("prompt_tokens", 0),
        "completion_tokens": r.get("completion_tokens", 0),
        "models_used": r.get("models_used", []),
        "answer": r["obs"]["answer"],
    }
    if "unexpected" in s: rec["unexpected"] = s["unexpected"]
    if "garbage" in s: rec["garbage"] = s["garbage"]
    return rec


def merge_into_bench(bj_path, new_rec):
    b = json.loads(bj_path.read_text(encoding="utf-8"))
    cases = b["cases"]
    # idempotent replace
    cases = [c for c in cases if c["id"] != NEW_ID]
    cases.append(new_rec)
    b["cases"] = cases
    b["n"] = len(cases)
    total_cost = round(sum(c.get("cost", 0) for c in cases), 6)
    b["total_cost_usd"] = total_cost
    b["cost_per_q_usd"] = round(total_cost / len(cases), 6) if cases else 0
    b["avg_elapsed_s"] = round(sum(c.get("elapsed", 0) for c in cases) / len(cases), 1) if cases else 0
    passed = sum(1 for c in cases if c.get("pass"))
    b["passed"] = passed
    b["pass_rate"] = round(passed / len(cases), 4) if cases else 0
    by_cat = {}
    for c in cases:
        d = by_cat.setdefault(c.get("category", "?"), {"passed": 0, "total": 0})
        d["total"] += 1
        if c.get("pass"): d["passed"] += 1
    b["by_category"] = by_cat
    bj_path.write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")
    return passed, len(cases)


def run_single(model, tmp_cases):
    sl = BM.slug(model)
    out_dir = BM.BENCH_DIR / sl
    bj = out_dir / "bench.json"
    if not bj.exists():
        print(f"  !! no existing bench.json for {sl}, skip", flush=True); return None
    existing = json.loads(bj.read_text(encoding="utf-8"))
    if any(c["id"] == NEW_ID for c in existing.get("cases", [])):
        print(f"  SKIP {model} ({NEW_ID} already present)", flush=True)
        return existing
    print(f"\n========== {NEW_ID} :: {model} ==========", flush=True)
    BM.set_model(model)
    if not BM.restart_and_wait():
        print(f"  !! service unhealthy after restart, skip {model}", flush=True); return None
    served = BM.current_served_model()
    print(f"  served: {served}", flush=True)
    since = time.time()
    tmp_out = out_dir / "_refuse_run"
    tmp_out.mkdir(parents=True, exist_ok=True)
    log = open(tmp_out / "run.log", "w")
    subprocess.run([sys.executable, str(EVAL / "run_eval.py"),
                    "--base", BASE, "--out", str(tmp_out), "--cases", str(tmp_cases)],
                   stdout=log, stderr=subprocess.STDOUT, cwd=str(EVAL))
    log.close()
    res_path = tmp_out / "results.jsonl"
    if not res_path.exists():
        print("  !! no results.jsonl", flush=True); return None
    results = [json.loads(l) for l in res_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not results:
        print("  !! empty results", flush=True); return None
    usage = BM.load_usage(since)
    results = BM.attribute_cost(results, usage)
    meta = cmeta()
    rec = build_case_record(results[0], meta)
    passed, n = merge_into_bench(bj, rec)
    print(f"  → {NEW_ID}: pass={rec['pass']} cost=${rec['cost']:.5f} tools={rec['tools']} | bench now {passed}/{n}", flush=True)
    print(f"     answer: {rec['answer'][:240].replace(chr(10),' ')}", flush=True)
    return rec


def main():
    env_backup = ENV.read_text(encoding="utf-8")
    (BM.ROOT / ".env.benchbak").write_text(env_backup, encoding="utf-8")
    print(f"backed up .env ({len(env_backup)} bytes)", flush=True)

    def handler(signum, frame):
        try:
            ENV.write_text(env_backup, encoding="utf-8")
            subprocess.run(["pkill", "-f", "run_eval.py"])
            BM.restart_and_wait(timeout=30)
            print(f"\n!! signal {signum} → restored .env + restarted", flush=True)
        finally:
            os._exit(1)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)

    # temp single-case file
    refuse_line = None
    for l in CASES.read_text(encoding="utf-8").splitlines():
        if l.strip() and json.loads(l)["id"] == NEW_ID:
            refuse_line = l.strip(); break
    if not refuse_line:
        print("FATAL: refuse_unknown_year not in cases.jsonl"); sys.exit(1)
    tf = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, dir=str(EVAL))
    tf.write(refuse_line + "\n"); tf.close()
    tmp_cases = Path(tf.name)
    print(f"temp cases file: {tmp_cases}", flush=True)

    models = sys.argv[1:] or BM.MODELS
    try:
        for m in models:
            run_single(m, tmp_cases)
    finally:
        ENV.write_text(env_backup, encoding="utf-8")
        BM.restart_and_wait()
        try: tmp_cases.unlink()
        except Exception: pass
        print("restored original .env + restarted (live site back to prod model)", flush=True)
    print("SWEEP DONE", flush=True)


if __name__ == "__main__":
    main()
