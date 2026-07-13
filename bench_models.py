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
EVAL = ROOT / "eval"
# версии бенча = папки eval/bench_v1, bench_v2, … (внутри cases.jsonl + ответы)
BENCH_DIR = sorted(EVAL.glob('bench_v*'), key=lambda p: (len(p.name), p.name))[-1]
CASES = EVAL / "cases.jsonl"


def prepare_bench_dir(allow_drift=False):
    """Папка версии самодостаточна: cases.jsonl снапшотится при первом прогоне.
    Датасет уехал от снапшота → это уже ДРУГОЙ бенч: гони с --version v<N+1>."""
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    snap = BENCH_DIR / "cases.jsonl"
    cur = CASES.read_text(encoding="utf-8")
    if not snap.exists():
        snap.write_text(cur, encoding="utf-8")
        print(f"  датасет снапшотнут в {snap}", flush=True)
        return True
    if snap.read_text(encoding="utf-8") == cur:
        return True
    msg = (f"!! eval/cases.jsonl отличается от {snap} — это уже другая версия бенча.\n"
           f"   Запусти с --version v<следующий> (папка создастся сама).")
    if allow_drift:
        print(msg + "\n   --allow-dataset-drift: ЕДУ ДАЛЬШЕ ПО ТВОЕМУ ПРИКАЗУ", flush=True)
        return True
    print(msg, flush=True)
    return False

# Цель прогона. По умолчанию — прод (тестируем ровно то, что видит читатель).
# --dev переключает на dev-стенд: прод не трогаем вообще (ни .env, ни рестарты).
TARGETS = {
    "prod": {
        "env": ROOT / ".env",
        "service": "sigma-assistant.service",
        "port": 8766,
        "base": "https://sigma.fmin.xyz",
        "usage_log": ROOT / "eval" / "usage_log.jsonl",
    },
    "dev": {
        "env": Path("/root/sigma_assistant_dev/.env"),
        "service": "sigma-assistant-dev.service",
        "port": 8767,
        "base": "https://sigmadev.fmin.xyz",
        "usage_log": Path("/root/sigma_assistant_dev/eval/usage_log.jsonl"),
    },
}
T = TARGETS["prod"]
ENV = T["env"]
USAGE_LOG = T["usage_log"]
HEALTH = f"http://127.0.0.1:{T['port']}/healthz"
BASE = T["base"]


def use_target(name):
    global T, ENV, USAGE_LOG, HEALTH, BASE
    T = TARGETS[name]
    ENV = T["env"]
    USAGE_LOG = T["usage_log"]
    HEALTH = f"http://127.0.0.1:{T['port']}/healthz"
    BASE = T["base"]

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
    subprocess.run(["systemctl", "restart", T["service"]], check=True)
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
        return json.load(urllib.request.urlopen(f"http://127.0.0.1:{T['port']}/api/model", timeout=5))
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


def save_figures(out_dir, case_id, figures):
    """Каждую картинку агента — в файл figs/<case>_<i>.png; в bench.json
    остаётся относительный путь. base64 в JSON не храним: файлы видны в репо,
    их отдаёт /benchmark/shots/, и bench.json не разбухает на мегабайты."""
    import base64
    saved = []
    figdir = out_dir / "figs"
    for i, f in enumerate(figures or []):
        if f.startswith("data:image/"):
            try:
                head, b64 = f.split(",", 1)
                ext = "png" if "png" in head else ("jpg" if "jpe" in head else "png")
                figdir.mkdir(parents=True, exist_ok=True)
                p = figdir / f"{case_id}_{i}.{ext}"
                p.write_bytes(base64.b64decode(b64))
                saved.append(f"figs/{p.name}")
            except Exception as e:
                print(f"  !! figure save failed ({case_id}_{i}): {e}", flush=True)
        elif f.startswith("figs/"):
            saved.append(f)  # уже файл
        # /figures/<uuid> и прочие внешние ссылки не переживают прогон — дропаем
    return saved


def bench_one(model, force=False):
    sl = slug(model)
    out_dir = BENCH_DIR / sl
    bj = out_dir / "bench.json"
    if bj.exists() and not force:
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
    # полные ответы тулзов из серверного лога диалогов → в trace кейсов
    # (bench.json собирается ниже уже с обогащённым results? нет — attach работает
    # по готовому bench.json, поэтому зовём его в самом конце bench_one)
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
        "bench_version": BENCH_DIR.name.replace("bench_", ""),
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
            "figures": save_figures(out_dir, cmeta_of(r)["id"], r["obs"].get("figures", [])),
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
    try:
        sys.path.insert(0, str(ROOT / "eval"))
        from attach_tool_results import attach, load_convo
        convo = load_convo(Path(os.environ.get("SIGMA_CONVO_LOG", "")) if os.environ.get("SIGMA_CONVO_LOG")
                           else ROOT / "eval" / ("llm_log_dev.jsonl" if T is TARGETS["dev"] else "llm_log.jsonl"))
        n = attach(out_dir, convo)
        print(f"  подшиты полные ответы тулзов: {n} кейсов", flush=True)
    except Exception as e:
        print(f"  !! attach_tool_results failed: {e}", flush=True)
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
    import argparse
    ap = argparse.ArgumentParser(
        description="Прогнать бенч по списку моделей одной командой. "
                    "Все артефакты — в eval/bench_v<N>/<slug>/: bench.json (+трейс, стоимость), "
                    "<case>.png (скрин живой страницы), figs/*.png (графики агента), "
                    "results.jsonl, run.log, report.md")
    ap.add_argument("models", nargs="*", help="id моделей OpenRouter; пусто = дефолтный список MODELS")
    ap.add_argument("--dev", action="store_true", help="гонять на dev-стенде (sigmadev, свой сервис/ключ) — прод не трогается")
    ap.add_argument("--force", action="store_true", help="перепрогнать даже если bench.json уже есть")
    ap.add_argument("--version", default=None, help="версия бенча (папка eval/bench_<v>); по умолчанию — последняя")
    ap.add_argument("--allow-dataset-drift", action="store_true",
                    help="гнать несмотря на расхождение cases.jsonl со снапшотом версии (результаты несравнимы!)")
    args = ap.parse_args()
    if args.version:
        global BENCH_DIR
        BENCH_DIR = EVAL / f"bench_{args.version}"
    if args.dev:
        use_target("dev")
    print(f"target: {BASE} (service {T['service']}) · {BENCH_DIR.name}", flush=True)
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    if not prepare_bench_dir(allow_drift=args.allow_dataset_drift):
        sys.exit(2)
    env_backup = ENV.read_text(encoding="utf-8")  # verbatim snapshot for exact restore
    (ROOT / ".env.benchbak").write_text(env_backup, encoding="utf-8")
    print(f"backed up .env ({len(env_backup)} bytes) → .env.benchbak", flush=True)
    _install_signal_restore(env_backup)
    models = args.models or MODELS
    try:
        for model in models:
            bench_one(model, force=args.force)
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
