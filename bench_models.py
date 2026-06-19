#!/usr/bin/env python3
"""Multi-model benchmark of the LIVE Sigma agent.

Invariant the user cares about: the agent under test is EXACTLY the one on the
site (same assistant.js, same tools, same loop). The ONLY thing swapped per run
is SIGMA_MODEL on the server. We do that by editing /root/sigma_assistant/.env,
restarting sigma-assistant.service, and running the standard eval against the
live site. Cost is taken from REAL OpenRouter usage (server logs usage.cost to
eval/usage_log.jsonl); never estimated.

Output: eval/bench/<slug>/bench.json per model, consumed by gen_benchmark_page.py.
"""
import json, os, re, signal, subprocess, sys, time, urllib.request
from pathlib import Path

ROOT = Path("/root/sigma_assistant")
ENV = ROOT / ".env"
USAGE_LOG = ROOT / "eval" / "usage_log.jsonl"
BENCH_DIR = ROOT / "eval" / "bench"
CASES = ROOT / "eval" / "cases.jsonl"
HEALTH = "http://127.0.0.1:8766/healthz"
BASE = "https://sigma.fmin.xyz"

# Cheaper-than-current, tool-capable models. (V) = vision-capable (needed for
# compute_plot / vision_refine cases; non-vision models will visibly fail those).
# REAL models from the live OpenRouter catalogue (verified ids/prices, Apr–Jun 2026).
# All vision+tools except deepseek (text-only → will fail the 6 graphics/vision cases,
# included by explicit request to see its facts/compute strength vs that tradeoff).
MODELS = [
    "google/gemini-3.5-flash",          # reference (prod)   $1.50/$9.00 per M, vision
    "google/gemini-3.1-flash-lite",     # $0.25/$1.50        Google, vision
    "qwen/qwen3.6-flash",               # $0.19/$1.12        🇨🇳 Alibaba, vision
    "xiaomi/mimo-v2.5",                 # $0.14/$0.28        🇨🇳 Xiaomi, vision (cheapest)
    "stepfun/step-3.7-flash",           # $0.20/$1.15        🇨🇳 StepFun, vision
    "moonshotai/kimi-k2.6:free",        # FREE               🇨🇳 Moonshot, vision
    "google/gemma-4-26b-a4b-it:free",   # FREE               Google, vision
]


def slug(m):
    return re.sub(r"[^a-z0-9]+", "_", m.lower()).strip("_")


def set_model(model):
    txt = ENV.read_text(encoding="utf-8")
    def repl(key, val, t):
        if re.search(rf"(?m)^{key}=", t):
            return re.sub(rf"(?m)^{key}=.*$", f"{key}={val}", t)
        return t.rstrip("\n") + f"\n{key}={val}\n"
    txt = repl("SIGMA_MODEL", model, txt)
    txt = repl("SIGMA_VISION_MODEL", model, txt)
    ENV.write_text(txt, encoding="utf-8")


def restart_and_wait(timeout=90):
    subprocess.run(["systemctl", "restart", "sigma-assistant.service"], check=True)
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            d = json.load(urllib.request.urlopen(HEALTH, timeout=5))
            if d.get("ok"):
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def current_served_model():
    try:
        return json.load(urllib.request.urlopen("http://127.0.0.1:8766/api/model", timeout=5))
    except Exception:
        return None


def run_eval(out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    log = open(out_dir / "run.log", "w")
    subprocess.run([sys.executable, str(ROOT / "eval" / "run_eval.py"),
                    "--base", BASE, "--out", str(out_dir)],
                   stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT / "eval"))
    log.close()


def load_usage(since_ts):
    rows = []
    if not USAGE_LOG.exists():
        return rows
    for l in USAGE_LOG.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        try:
            r = json.loads(l)
        except Exception:
            continue
        if r.get("ts", 0) >= since_ts - 1:
            rows.append(r)
    return rows


def attribute_cost(results, usage_rows):
    """Sum real OpenRouter cost into each case by its [t_start, t_end] window."""
    for r in results:
        t0, t1 = r.get("t_start", 0), r.get("t_end", 0) + 2
        cost = 0.0
        ptok = ctok = 0
        models_used = set()
        for u in usage_rows:
            if t0 <= u.get("ts", 0) <= t1:
                if u.get("cost") is not None:
                    cost += float(u["cost"])
                ptok += u.get("prompt_tokens") or 0
                ctok += u.get("completion_tokens") or 0
                if u.get("model"):
                    models_used.add(u["model"])
        r["cost"] = round(cost, 6)
        r["prompt_tokens"] = ptok
        r["completion_tokens"] = ctok
        r["models_used"] = sorted(models_used)
    return results


def bench_one(model):
    sl = slug(model)
    out_dir = BENCH_DIR / sl
    bj = out_dir / "bench.json"
    if bj.exists():
        try:
            existing = json.loads(bj.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
        if not existing.get("provisional"):  # provisional (no real cost) → re-run
            print(f"\n========== SKIP {model} (bench.json exists) ==========", flush=True)
            return existing
    print(f"\n========== BENCH {model} ==========", flush=True)
    set_model(model)
    if not restart_and_wait():
        print(f"  !! service not healthy after restart, skipping {model}", flush=True)
        return None
    served = current_served_model()
    print(f"  served model: {served}", flush=True)
    # mark usage-log boundary
    since = time.time()
    t0 = time.time()
    run_eval(out_dir)
    # load eval results + attribute real cost
    res_path = out_dir / "results.jsonl"
    if not res_path.exists():
        print("  !! no results.jsonl", flush=True)
        return None
    results = [json.loads(l) for l in res_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    usage = load_usage(since)
    results = attribute_cost(results, usage)
    # results.jsonl carries case_id (not the full case) → join with cases.jsonl
    cmeta = {c["id"]: c for c in (json.loads(l) for l in CASES.read_text(encoding="utf-8").splitlines() if l.strip())}
    def cmeta_of(r):
        return cmeta.get(r.get("case_id"), {"id": r.get("case_id"), "category": "?", "question": r.get("case_id", "")})
    # assemble bench.json
    by_cat = {}
    for r in results:
        c = cmeta_of(r)["category"]
        by_cat.setdefault(c, {"pass": 0, "total": 0})
        by_cat[c]["total"] += 1
        by_cat[c]["pass"] += 1 if r["score"]["pass"] else 0
    total = len(results)
    passed = sum(1 for r in results if r["score"]["pass"])
    no_answer = sum(1 for r in results if r["obs"].get("no_answer"))
    total_cost = round(sum(r.get("cost", 0) for r in results), 6)
    bench = {
        "model": model,
        "served_model_label": served,
        "ran_at": t0,
        "n": total,
        "passed": passed,
        "answered": total - no_answer,   # cases where the eval actually got an answer
        "no_answer": no_answer,          # DNF: empty after retries (rate-limit/cutoff), NOT a content fail
        "pass_rate": round(passed / total, 4) if total else 0,
        "total_cost_usd": total_cost,
        "cost_per_q_usd": round(total_cost / total, 6) if total else 0,
        "avg_elapsed_s": round(sum(r.get("elapsed", 0) for r in results) / total, 1) if total else 0,
        "by_category": by_cat,
        "cases": [{
            "id": cmeta_of(r)["id"],
            "category": cmeta_of(r)["category"],
            "question": cmeta_of(r)["question"],
            "pass": r["score"]["pass"],
            "tool_match": r["score"]["tool_match"],
            "answer_match": r["score"]["answer_match"],
            "visual_match": r["score"]["visual_match"],
            "missing": r["score"].get("missing"),
            "tools": [t["tool"] for t in r["obs"]["trace"]],
            # full trace (tool + args) so the python CODE that drew a figure is
            # saved — previously only names were kept, so figures couldn't be
            # regenerated. Figures themselves stored as base64 data-URLs.
            "trace": r["obs"]["trace"],
            "images": r["obs"]["images"],
            "figures": r["obs"].get("figures", []),
            "elapsed": round(r.get("elapsed", 0), 1),
            "cost": r.get("cost", 0),
            "prompt_tokens": r.get("prompt_tokens", 0),
            "completion_tokens": r.get("completion_tokens", 0),
            "models_used": r.get("models_used", []),
            "answer": r["obs"]["answer"],
            "no_answer": r["obs"].get("no_answer", False),
        } for r in results],
    }
    (out_dir / "bench.json").write_text(json.dumps(bench, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  → {passed}/{total} pass · ${total_cost:.4f} total · ${bench['cost_per_q_usd']:.5f}/q", flush=True)
    return bench


def _install_signal_restore(env_backup):
    """SIGTERM/SIGINT must NOT leave the live site stuck on a test model.
    Python doesn't run finally: on SIGTERM, so restore .env + kill any child
    eval + restart explicitly, then exit. (Incident 2026-06-09: killed sweep
    left .env on qwen and orphaned run_eval hammering prod.)"""
    def handler(signum, frame):
        try:
            ENV.write_text(env_backup, encoding="utf-8")
            subprocess.run(["pkill", "-f", "run_eval.py"])
            restart_and_wait(timeout=30)
            print(f"\n!! signal {signum} → restored .env + restarted, exiting", flush=True)
        finally:
            os._exit(1)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def main():
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    env_backup = ENV.read_text(encoding="utf-8")  # verbatim snapshot for exact restore
    (ROOT / ".env.benchbak").write_text(env_backup, encoding="utf-8")
    print(f"backed up .env ({len(env_backup)} bytes) → .env.benchbak", flush=True)
    _install_signal_restore(env_backup)
    models = sys.argv[1:] or MODELS
    try:
        for model in models:
            bench_one(model)
            # regenerate the public page after each model so it fills in live
            try:
                subprocess.run([sys.executable, str(ROOT / "gen_benchmark_page.py")], check=False)
            except Exception as e:
                print(f"  page-regen failed: {e}", flush=True)
    finally:
        # restore the ORIGINAL .env verbatim + restart so the live site is normal
        ENV.write_text(env_backup, encoding="utf-8")
        restart_and_wait()
        print("restored original .env + restarted", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
