#!/usr/bin/env python3
"""Targeted, honest re-run of the DNF (no_answer) cases per model.

Why this exists: a handful of cases finished with NO answer after the harness's
own retries (free-tier hang / API timeout / cutoff). Those scored 0, but a 0 is
only fair if the model truly fails at a CORRECT invocation — not if the eval
pipeline hiccuped. Per Daniil: "либо добиваться ответа, либо если она никак не
отвечает при корректном запуске — просто 0". So we re-ask each DNF case against
its own model, with the standard harness retries, and MERGE the fresh result
back into that model's bench.json. We also capture the sigma-assistant journal
window for each model so every remaining 0 has a diagnosis (did the proxy even
get the request? did OpenRouter error/rotate?) — model-fault vs harness-fault.

The live site is swapped one model at a time (set_model → restart → eval --only)
and ALWAYS restored to the prod model at the end, even on SIGTERM/SIGINT.
"""
import asyncio, json, os, signal, subprocess, sys, time
from pathlib import Path

ROOT = Path("/root/sigma_assistant")
sys.path.insert(0, str(ROOT))            # for bench_models
sys.path.insert(0, str(ROOT / "eval"))   # for run_eval, ru_stem
import bench_models as bm  # set_model, restart_and_wait, current_served_model, load_usage, attribute_cost, slug, CASES, BENCH_DIR, BASE
import run_eval as RE      # the eval harness (run_eval(cases_path, base_url, out_dir, only=...))

PROD_MODEL = "qwen/qwen3.5-9b"   # restore the live site to this at the end
DIAG_DIR = ROOT / "eval" / "_rerun_diag"
DIAG_DIR.mkdir(exist_ok=True)

CASES_META = {c["id"]: c for c in (json.loads(l) for l in bm.CASES.read_text(encoding="utf-8").splitlines() if l.strip())}


def dnf_map():
    """Read every bench.json, return {model: ([case_ids], bench_path)} for DNFs."""
    out = {}
    for bj in sorted(bm.BENCH_DIR.glob("*/bench.json")):
        d = json.loads(bj.read_text(encoding="utf-8"))
        dnf = [c["id"] for c in d["cases"] if c.get("no_answer")]
        if dnf:
            out[d["model"]] = (dnf, bj)
    return out


def case_dict_from_result(r):
    """Rebuild a bench.json case entry from a results.jsonl row (same shape as
    bench_models.bench_one). r already has cost attributed."""
    cid = r.get("case_id")
    cm = CASES_META.get(cid, {"id": cid, "category": "?", "question": cid})
    s, obs = r["score"], r["obs"]
    return {
        "id": cm["id"], "category": cm["category"], "question": cm["question"],
        "pass": s["pass"], "tool_match": s["tool_match"], "answer_match": s["answer_match"],
        "visual_match": s["visual_match"], "missing": s.get("missing"),
        "tools": [t["tool"] for t in obs["trace"]], "trace": obs["trace"],
        "images": obs["images"], "figures": obs.get("figures", []),
        "elapsed": round(r.get("elapsed", 0), 1), "cost": r.get("cost", 0),
        "prompt_tokens": r.get("prompt_tokens", 0), "completion_tokens": r.get("completion_tokens", 0),
        "models_used": r.get("models_used", []), "answer": obs["answer"],
        "no_answer": obs.get("no_answer", False),
    }


def recompute_aggregates(bench):
    cases = bench["cases"]
    total = len(cases)
    passed = sum(1 for c in cases if c["pass"])
    no_answer = sum(1 for c in cases if c.get("no_answer"))
    by_cat = {}
    for c in cases:
        by_cat.setdefault(c["category"], {"pass": 0, "total": 0})
        by_cat[c["category"]]["total"] += 1
        by_cat[c["category"]]["pass"] += 1 if c["pass"] else 0
    total_cost = round(sum(c.get("cost", 0) for c in cases), 6)
    bench.update({
        "n": total, "passed": passed, "answered": total - no_answer, "no_answer": no_answer,
        "pass_rate": round(passed / total, 4) if total else 0,
        "total_cost_usd": total_cost,
        "cost_per_q_usd": round(total_cost / total, 6) if total else 0,
        "avg_elapsed_s": round(sum(c.get("elapsed", 0) for c in cases) / total, 1) if total else 0,
        "by_category": by_cat,
    })
    return bench


def rerun_model(model, case_ids, bench_path):
    print(f"\n========== RE-RUN {model}  DNF={case_ids} ==========", flush=True)
    # Resume: if a completed diag run for these exact cases already exists (e.g. a
    # prior crash AFTER the live eval but BEFORE merge), merge it instead of paying
    # for the live eval again. Cost re-attributes fine — usage_log.jsonl is durable.
    out_dir = DIAG_DIR / bm.slug(model)
    rp = out_dir / "results.jsonl"
    if rp.exists():
        have = {json.loads(l)["case_id"] for l in rp.read_text(encoding="utf-8").splitlines() if l.strip()}
        if set(case_ids) <= have:
            print(f"  [resume] diag results present for all {len(case_ids)} cases → merge only, no re-eval", flush=True)
            rows = [json.loads(l) for l in rp.read_text(encoding="utf-8").splitlines() if l.strip()]
            since = min(r.get("t_start", time.time()) for r in rows)
            return merge_from_diag(model, case_ids, bench_path, out_dir, since)
    bm.set_model(model)
    if not bm.restart_and_wait():
        print(f"  !! not healthy after restart, SKIP {model}", flush=True)
        return None
    served = bm.current_served_model()
    print(f"  served: {served}", flush=True)
    since = time.time()
    out_dir = DIAG_DIR / bm.slug(model)
    # Run only the DNF cases against the live (now-swapped) site.
    asyncio.run(RE.run_eval(bm.CASES, bm.BASE, out_dir, only=case_ids))
    return merge_from_diag(model, case_ids, bench_path, out_dir, since)


def merge_from_diag(model, case_ids, bench_path, out_dir, since):
    """Attribute cost + merge a completed diag run into bench.json. Split out so a
    crashed merge can be re-applied without re-running the (expensive) live eval."""
    # Capture the server journal for this window → per-case diagnosis.
    jpath = out_dir / "journal.txt"
    try:
        j = subprocess.run(["journalctl", "-u", "sigma-assistant.service",
                            "--since", f"@{int(since)}", "--no-pager"],
                           capture_output=True, text=True, timeout=30).stdout
        jpath.write_text(j, encoding="utf-8")
    except Exception as e:
        jpath.write_text(f"[journal capture failed: {e}]", encoding="utf-8")
    # Load fresh results, attribute real cost.
    res_path = out_dir / "results.jsonl"
    if not res_path.exists():
        print("  !! no results.jsonl", flush=True)
        return None
    results = [json.loads(l) for l in res_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    results = bm.attribute_cost(results, bm.load_usage(since))
    new_cases = {r["case_id"]: case_dict_from_result(r) for r in results}
    # Merge into the existing bench.json (replace only the re-run cases).
    bench = json.loads(bench_path.read_text(encoding="utf-8"))
    changed = []
    for c in bench["cases"]:
        cid = c["id"]                      # capture BEFORE clear() empties the dict
        if cid in new_cases:
            old_na = c.get("no_answer")
            c.clear(); c.update(new_cases[cid])
            changed.append((cid, old_na, c.get("no_answer"), c.get("pass")))
    recompute_aggregates(bench)
    bench["rerun_dnf_at"] = since
    bench_path.write_text(json.dumps(bench, ensure_ascii=False, indent=1), encoding="utf-8")
    for cid, old_na, new_na, ok in changed:
        verdict = "RECOVERED→answered" if old_na and not new_na else ("still DNF" if new_na else "answered")
        print(f"    {cid:30s} {verdict:20s} pass={ok}", flush=True)
    return changed


def main():
    env_backup = bm.ENV.read_text(encoding="utf-8")

    def restore(signum=None, frame=None):
        bm.set_model(PROD_MODEL)
        subprocess.run(["pkill", "-f", "run_eval.py"])
        bm.restart_and_wait(timeout=60)
        if signum:
            print(f"\n!! signal {signum} → restored prod model + restarted, exiting", flush=True)
            os._exit(1)
    signal.signal(signal.SIGTERM, restore)
    signal.signal(signal.SIGINT, restore)

    dm = dnf_map()
    print(f"DNF map: { {m: ids for m,(ids,_) in dm.items()} }", flush=True)
    summary = {}
    try:
        for model, (ids, bj) in dm.items():
            changed = rerun_model(model, ids, bj)
            summary[model] = changed
            try:
                subprocess.run([sys.executable, str(ROOT / "gen_benchmark_page.py")], check=False)
            except Exception as e:
                print(f"  page-regen failed: {e}", flush=True)
    finally:
        bm.set_model(PROD_MODEL)
        bm.restart_and_wait()
        print(f"\nrestored live model → {PROD_MODEL} + restarted", flush=True)
    print("\n===== SUMMARY =====", flush=True)
    for m, ch in summary.items():
        if not ch:
            print(f"  {m}: (no merge)"); continue
        rec = sum(1 for _, old, new, _ in ch if old and not new)
        still = sum(1 for _, _, new, _ in ch if new)
        print(f"  {m}: recovered={rec} stillDNF={still} of {len(ch)}")
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
