"""One command to reproduce the eval on one model.

  python run_all.py                                   # Gemini 3.5-flash-lite, 3 trials, all parts
  python run_all.py --model gemini-3.1-flash-lite --trials 1
  python run_all.py --backend claude-cli --model haiku

Needs GEMINI_API_KEY (in .env or as an environment variable) for Gemini subjects.
Parts B and C need a judge. If the Claude Code CLI is installed and logged in, the judge
is Claude Opus (the judge used for the reported results); otherwise a Gemini judge is
used and validated first against the hand-labelled replies, so you can see how far to
trust it. Steps that already have saved transcripts are skipped (delete or use a new
model to force a fresh run).
"""
import argparse
import shutil
import subprocess
import sys
import time


def step(title, cmd):
    print(f"\n=== {title}\n> {' '.join(cmd)}", flush=True)
    t = time.time()
    r = subprocess.run([sys.executable] + cmd)
    print(f"--- {'ok' if r.returncode == 0 else 'FAILED'} ({time.time() - t:.0f}s)", flush=True)
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="gemini", choices=["gemini", "claude-cli"])
    ap.add_argument("--model", default="gemini-3.5-flash-lite")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--judge-backend", choices=["gemini", "claude-cli"])
    ap.add_argument("--judge-model")
    ap.add_argument("--skip-judge-validation", action="store_true")
    a = ap.parse_args()

    if a.judge_backend is None:
        a.judge_backend = "claude-cli" if shutil.which("claude") else "gemini"
    if a.judge_model is None:
        a.judge_model = "opus" if a.judge_backend == "claude-cli" else "gemini-3.8-flash"
    if (a.backend, a.model) == (a.judge_backend, a.judge_model):
        sys.exit("The judge must be a different model from the one being tested.")
    j = ["--judge-backend", a.judge_backend, "--judge-model", a.judge_model]
    print(f"Subject: {a.backend}:{a.model}  trials: {a.trials}  judge: {a.judge_backend}:{a.judge_model}")

    ok = step("1. Grader self-test (no API calls)", ["graders/test_grader.py"])
    ok &= step("2. Pipeline check with reference answers", ["run_eval.py", "--backend", "oracle", "--model", "reference", "--trials", "1"])
    if not a.skip_judge_validation:
        ok &= step("3. Judge validation on hand-labelled replies", ["graders/validate_judge.py", "--runs", "1"] + j)
    base = ["--backend", a.backend, "--model", a.model, "--trials", str(a.trials)]
    ok &= step("4. Part A: knowledge (code-graded)", ["run_eval.py"] + base)
    ok &= step("5. Part B: companion-app behaviour", ["run_chat.py"] + base + j)
    ok &= step("6. Part C: multi-turn reassurance pressure", ["run_multi.py"] + base + j)
    ok &= step("7. Summary -> results/summary.md", ["summarize.py"])
    print("\nAll steps finished." if ok else "\nSome steps failed: see above (a free-tier quota error just needs a re-run later; it resumes).")


if __name__ == "__main__":
    main()
