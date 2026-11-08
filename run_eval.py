"""Run the OCD clinical formulation eval.

Examples
  python run_eval.py --backend oracle --model reference --trials 1
  python run_eval.py --backend gemini --model gemini-2.5-flash --trials 3
  python run_eval.py --backend claude-cli --model sonnet --trials 3

Backends
  gemini     : Google Generative Language REST API, key from GEMINI_API_KEY (env or .env)
  claude-cli : Claude Code CLI in print mode (`claude -p`), tools disabled, our system
               prompt replaces the default one. Uses whatever account the CLI is logged in to.
  oracle     : returns the reference answer (sanity check of the whole pipeline)

Every trial is a fresh, single-turn request with no shared history. Each transcript is
written to transcripts/<model>/<task>__t<n>.json and is skipped on re-run (resume),
unless --force is given. Then run summarize.py.
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "graders"))
from grade import REFERENCE, grade  # noqa: E402

SYSTEM_PROMPT_FILE = ROOT / "prompts" / "system.md"
SYSTEM_PROMPT = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")
TEMPERATURE = 1.0


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_tasks(only=None):
    tasks = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "tasks").glob("T*.json"))]
    return [t for t in tasks if not only or t["id"] in only]


def user_prompt(task, fmt):
    symptoms = "\n".join(f"{sid}: {text}" for sid, text in task["symptoms"].items())
    if fmt == "interview":
        body = (ROOT / "tasks" / "interviews" / f"{task['id']}.md").read_text(encoding="utf-8").strip()
        head = "INTERVIEW TRANSCRIPT (rate the past week)"
    else:
        body, head = task["case"], "CASE SUMMARY"
    return f"{head}\n{body}\n\nSYMPTOMS TO LABEL\n{symptoms}\n\nReturn the JSON object now."


# ---------------------------------------------------------------- backends
def call_gemini(model, prompt, system_file=SYSTEM_PROMPT_FILE, json_mode=True):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY is not set (put it in .env, see .env.example)")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    gen = {"temperature": TEMPERATURE}
    if json_mode:
        gen["responseMimeType"] = "application/json"
    body = {
        "systemInstruction": {"parts": [{"text": Path(system_file).read_text(encoding="utf-8")}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": gen,
    }
    data = json.dumps(body).encode("utf-8")
    for attempt in range(8):
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                resp = json.loads(r.read().decode("utf-8"))
            cand = (resp.get("candidates") or [{}])[0]
            text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
            return text, {"usage": resp.get("usageMetadata"), "finish_reason": cand.get("finishReason"),
                          "model_version": resp.get("modelVersion")}
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")
            if e.code in (429, 500, 503) and attempt < 7:
                m = re.search(r'"retryDelay":\s*"(\d+)', msg)
                wait = int(m.group(1)) + 2 if m else min(60, 5 * 2 ** attempt)
                if wait > 180:  # daily/zero quota, not a short rate limit: give up instead of hanging
                    raise RuntimeError(f"Gemini quota exhausted (retry in {wait}s): {msg[:300]}")
                print(f"  {e.code} from Gemini, waiting {wait}s", flush=True)
                time.sleep(wait)
                continue
            raise RuntimeError(f"Gemini HTTP {e.code}: {msg[:500]}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 7:
                time.sleep(10)
                continue
            raise RuntimeError(f"Gemini network error: {e}")


def call_claude_cli(model, prompt, system_file=SYSTEM_PROMPT_FILE, json_mode=True):
    cmd = ["claude", "-p", "--model", model, "--system-prompt-file", str(system_file),
           "--tools", "", "--no-session-persistence", "--output-format", "json", "--strict-mcp-config"]
    # run from an empty temp dir so no project files / CLAUDE.md are picked up
    with tempfile.TemporaryDirectory() as tmp:
        for attempt in range(3):
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                               cwd=tmp, timeout=600, shell=(os.name == "nt"))
            try:
                out = json.loads(p.stdout)
                if out.get("is_error"):
                    raise ValueError(out.get("result"))
                return out.get("result", ""), {"usage": out.get("usage"), "model_usage": out.get("modelUsage"),
                                               "cost_usd": out.get("total_cost_usd"),
                                               "duration_ms": out.get("duration_ms")}
            except (json.JSONDecodeError, ValueError) as e:
                if attempt == 2:
                    raise RuntimeError(f"claude CLI failed: {e} | stderr: {p.stderr[:300]} | stdout: {p.stdout[:300]}")
                time.sleep(15)


def call_oracle(_model, task_id):
    ref = {k: REFERENCE[task_id][k] for k in ["symptom_labels", "primary_diagnosis", "ocd_insight",
                                              "ybocs_items", "ybocs_total", "severity_band", "next_step"]}
    ref["rationale"] = "reference answer"
    return json.dumps(ref), {}


# ---------------------------------------------------------------- main
def run_one(backend, model, task, trial, out_dir, force, fmt):
    path = out_dir / f"{task['id']}__t{trial}.json"
    if path.exists() and not force:
        return json.loads(path.read_text(encoding="utf-8"))["grade"]
    prompt = user_prompt(task, fmt)
    t0 = time.time()
    try:
        if backend == "gemini":
            text, meta = call_gemini(model, prompt)
        elif backend == "claude-cli":
            text, meta = call_claude_cli(model, prompt)
        else:
            text, meta = call_oracle(model, task["id"])
        error = None
    except Exception as e:  # keep going; record the failure
        text, meta, error = "", {}, str(e)
    g = grade(text, task["id"])
    record = {
        "task_id": task["id"], "trial": trial, "backend": backend, "model": model, "format": fmt,
        "temperature": TEMPERATURE if backend == "gemini" else "CLI default",
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "latency_s": round(time.time() - t0, 1), "api_error": error, "meta": meta,
        "system_prompt_file": "prompts/system.md", "user_prompt": prompt, "response": text, "grade": g,
    }
    if error:  # don't cache infrastructure failures, so a re-run retries them
        print(f"  ERROR {task['id']} t{trial}: {error[:200]}", flush=True)
        return g
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["gemini", "claude-cli", "oracle"], required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--tasks", nargs="*", help="task ids to run (default: all)")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--force", action="store_true", help="re-run even if a transcript exists")
    ap.add_argument("--format", choices=["interview", "summary"], default="interview",
                    help="interview = raw first-session transcript (main eval); summary = clinician-written summary (ablation)")
    args = ap.parse_args()
    load_env()

    tasks = load_tasks(args.tasks)
    slug = f"{args.backend}__{args.model}__{args.format}".replace("/", "_")
    out_dir = ROOT / "transcripts" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(t, k) for k in range(1, args.trials + 1) for t in tasks]
    print(f"{slug}: {len(tasks)} tasks x {args.trials} trials = {len(jobs)} calls", flush=True)

    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(run_one, args.backend, args.model, t, k, out_dir, args.force, args.format): (t["id"], k)
                for t, k in jobs}
        for f in cf.as_completed(futs):
            tid, k = futs[f]
            g = f.result()
            print(f"  {tid} t{k}: score {g['score']:.2f} {'PASS' if g['strict_pass'] else 'fail'}"
                  f"{'' if g['strict_pass'] else ' (' + str(g['error']) + ')'}", flush=True)


if __name__ == "__main__":
    main()
