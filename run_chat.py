"""Part B: companion-app behaviour. Generates a reply with the model under test,
then grades it with a rubric judge (a different model).

  python run_chat.py --backend claude-cli --model haiku --trials 3
  python run_chat.py --backend gemini --model gemini-2.5-flash --trials 3
  python run_chat.py --backend reference --model good   # validation: hand-written replies

Judge: --judge-backend / --judge-model (default claude-cli / opus). Never use the
same model as judge and subject. Transcripts: transcripts_chat/<backend>__<model>/.
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "graders"))
import run_eval  # noqa: E402
from judge_grade import JUDGE_PROMPT_FILE, judge_input, load_chat_tasks, score  # noqa: E402

COMPANION = ROOT / "prompts" / "companion_system.md"
REFERENCE_REPLIES = ROOT / "reference" / "chat_replies.json"


def subject_prompt(task):
    return f"{task['conversation']}\n\nWrite Ease's reply to the latest message."


def call(backend, model, prompt, system_file, json_mode):
    if backend == "gemini":
        return run_eval.call_gemini(model, prompt, system_file=system_file, json_mode=json_mode)
    if backend == "claude-cli":
        return run_eval.call_claude_cli(model, prompt, system_file=system_file, json_mode=json_mode)
    if backend == "openai":
        return run_eval.call_openai(model, prompt, system_file=system_file, json_mode=json_mode)
    raise ValueError(backend)


def judge(task, reply, jb, jm):
    text, meta = call(jb, jm, judge_input(task, reply), JUDGE_PROMPT_FILE, True)
    return text, meta


def run_one(args, task, trial, out_dir):
    path = out_dir / f"{task['id']}__t{trial}.json"
    if path.exists() and not args.force and not args.rejudge:
        return json.loads(path.read_text(encoding="utf-8"))["grade"]
    t0 = time.time()
    if path.exists() and args.rejudge:
        rec = json.loads(path.read_text(encoding="utf-8"))
        reply, meta = rec["response"], rec["meta"]
    elif args.backend == "reference":
        reply, meta = json.loads(REFERENCE_REPLIES.read_text(encoding="utf-8"))[args.model][task["id"]], {}
    else:
        try:
            reply, meta = call(args.backend, args.model, subject_prompt(task), COMPANION, False)
        except Exception as e:
            print(f"  ERROR subject {task['id']} t{trial}: {e}", flush=True)
            return None
    try:
        jtext, jmeta = judge(task, reply, args.judge_backend, args.judge_model)
    except Exception as e:
        print(f"  ERROR judge {task['id']} t{trial}: {e}", flush=True)
        return None
    g = score(task, jtext)
    rec = {"task_id": task["id"], "trial": trial, "backend": args.backend, "model": args.model,
           "judge": f"{args.judge_backend}:{args.judge_model}",
           "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "latency_s": round(time.time() - t0, 1), "system_prompt_file": "prompts/companion_system.md",
           "user_prompt": subject_prompt(task), "response": reply, "meta": meta,
           "judge_raw": jtext, "judge_meta": jmeta, "grade": g}
    path.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["gemini", "claude-cli", "openai", "reference"], required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--tasks", nargs="*")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--judge-backend", default="claude-cli", choices=["claude-cli", "gemini", "openai"])
    ap.add_argument("--judge-model", default="opus")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--rejudge", action="store_true", help="keep saved replies, re-run only the judge")
    ap.add_argument("--out", default="transcripts_chat")
    args = ap.parse_args()
    run_eval.load_env()
    if args.backend == args.judge_backend and args.model == args.judge_model:
        sys.exit("Judge and subject must be different models.")
    tasks = [t for tid, t in load_chat_tasks().items() if not args.tasks or tid in args.tasks]
    out_dir = ROOT / args.out / f"{args.backend}__{args.model}".replace("/", "_")
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(t, k) for k in range(1, args.trials + 1) for t in tasks]
    print(f"{out_dir.name}: {len(jobs)} replies, judge {args.judge_backend}:{args.judge_model}", flush=True)
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(run_one, args, t, k, out_dir): (t["id"], k) for t, k in jobs}
        for f in cf.as_completed(futs):
            tid, k = futs[f]
            g = f.result()
            if g:
                print(f"  {tid} t{k}: {g['score']:.2f} {'PASS' if g['strict_pass'] else 'fail ' + str(g['failed_checks'])}",
                      flush=True)


if __name__ == "__main__":
    main()
