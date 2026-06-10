import sys, time, json
from pathlib import Path
sys.path.insert(0, str(Path("/root/sigma_assistant")))
import bench_models as BM

MODEL = "meta-llama/llama-4-scout"
IDS = "compute_plot_newton,compute_plot_gd_vs_newton,vision_refine_diverging_sgd,linreg_simple,plot_sincos_overlay,plot_loss_landscape"
env_backup = BM.ENV.read_text(encoding="utf-8")
out = BM.ROOT / "eval" / "reports" / "_validate_llama4"
try:
    BM.set_model(MODEL)
    assert BM.restart_and_wait(), "service not healthy"
    print("served:", BM.current_served_model(), flush=True)
    import subprocess
    subprocess.run([sys.executable, str(BM.ROOT/"eval"/"run_eval.py"),
                    "--base", BM.BASE, "--out", str(out), "--only", IDS],
                   cwd=str(BM.ROOT/"eval"))
finally:
    BM.ENV.write_text(env_backup, encoding="utf-8")
    BM.restart_and_wait()
    print("restored .env + restarted", flush=True)
# summarize
res = out / "results.jsonl"
if res.exists():
    rows=[json.loads(l) for l in res.read_text().splitlines() if l.strip()]
    print("\n=== VALIDATION RESULT ===")
    for r in rows:
        a=(r['obs'].get('answer') or '').strip()
        print(f"{r['case_id']:32s} elapsed={r['elapsed']:6.1f}s no_answer={r['obs'].get('no_answer')} ans_len={len(a)} pass={r['score']['pass']}")
