#!/usr/bin/env python3
"""Full-benchmark new cheaper-than-prod models + replace the 2 rate-limited :free
models with their PAID endpoints. Runs AFTER gapfill_empties.py (don't run
concurrently — both switch SIGMA_MODEL on the live server).

The :free models (gemma-4, kimi) scored 0/29 & 1/29 purely because the free tier
was rate-limited to empty — their paid endpoints actually answer. The 6 new picks
are the top tool+vision models cheaper than prod (see cheaper_candidates.md), incl.
the standout gpt-5-mini (gpt-5-nano already beat prod at 13× lower cost).

Usage: python3 collect_new_cheap.py            # all of NEW
       python3 collect_new_cheap.py <id> ...   # specific model ids
"""
import sys, time
from pathlib import Path
EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL.parent)); sys.path.insert(0, str(EVAL))
import bench_models as BM
import signal, subprocess, os

NEW = [
    # free → paid endpoint (replace the rate-limited :free rows)
    "google/gemma-4-26b-a4b-it",
    "moonshotai/kimi-k2.6",
    # top cheaper-than-prod tool+vision picks not yet benchmarked
    "openai/gpt-4.1-nano",
    "openai/gpt-5-mini",
    "qwen/qwen3.5-flash-02-23",
    "google/gemini-2.5-flash",
    "google/gemini-3-flash-preview",
    "qwen/qwen3-vl-32b-instruct",
]


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

    models = sys.argv[1:] or NEW
    try:
        for i, m in enumerate(models, 1):
            print(f"\n########## [{i}/{len(models)}] {m} ##########", flush=True)
            t0 = time.time()
            try:
                BM.bench_one(m)
            except Exception as e:
                print(f"  !! {m} failed: {e}", flush=True)
            print(f"  [{m}] done in {time.time()-t0:.0f}s", flush=True)
    finally:
        BM.ENV.write_text(env_backup, encoding="utf-8")
        BM.restart_and_wait()
        print("restored original .env + restarted", flush=True)
    print("COLLECT NEW DONE", flush=True)


if __name__ == "__main__":
    main()
