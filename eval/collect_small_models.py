#!/usr/bin/env python3
"""Collect ANSWERS from many small models for the benchmark (29-case set).
Serial by necessity — only one SIGMA_MODEL on the live server at a time.
Does NOT regenerate the public page (substring grades are wrong; the
adversarial Claude-judge workflow re-grades afterwards). Restores .env at end.

Usage: python3 collect_small_models.py            # default small lineup
       python3 collect_small_models.py <id> <id>  # specific models
"""
import os, signal, subprocess, sys, time
from pathlib import Path
EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL.parent)); sys.path.insert(0, str(EVAL))
import bench_models as BM

SMALL = [
    "google/gemma-3-12b-it",
    "google/gemma-3-27b-it",
    "google/gemini-2.5-flash-lite",
    "openai/gpt-5-nano",
    "openai/gpt-4o-mini",
    "amazon/nova-lite-v1",
    "qwen/qwen3-vl-8b-instruct",
    "mistralai/mistral-small-3.2-24b-instruct",
    "bytedance-seed/seed-1.6-flash",
    "meta-llama/llama-4-scout",
    "qwen/qwen3.5-9b",
    "mistralai/ministral-8b-2512",
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

    models = sys.argv[1:] or SMALL
    try:
        for i, m in enumerate(models, 1):
            print(f"\n########## [{i}/{len(models)}] {m} ##########", flush=True)
            t0 = time.time()
            try:
                BM.bench_one(m)            # set_model → restart → run_eval → cost → bench.json
            except Exception as e:
                print(f"  !! {m} failed: {e}", flush=True)
            print(f"  [{m}] done in {time.time()-t0:.0f}s", flush=True)
    finally:
        BM.ENV.write_text(env_backup, encoding="utf-8")
        BM.restart_and_wait()
        print("restored original .env + restarted (live back to prod model)", flush=True)
    print("COLLECT DONE", flush=True)


if __name__ == "__main__":
    main()
