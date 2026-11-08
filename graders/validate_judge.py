"""Validate the Part B judge against hand-labelled replies (reference/chat_replies.json).

Each labelled reply is judged --runs times (default 2) to check that verdicts don't flip.
Reports per-check agreement with the expected labels, and saves everything to
results/judge_validation.json.

  python graders/validate_judge.py --judge-backend claude-cli --judge-model opus
"""
import argparse
import concurrent.futures as cf
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "graders"))
import run_eval  # noqa: E402
import run_chat  # noqa: E402
from judge_grade import load_chat_tasks, score  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge-backend", default="claude-cli")
    ap.add_argument("--judge-model", default="opus")
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args()
    run_eval.load_env()
    tasks = load_chat_tasks()
    replies = json.loads((ROOT / "reference" / "chat_replies.json").read_text(encoding="utf-8"))
    expected = json.loads((ROOT / "reference" / "chat_expected.json").read_text(encoding="utf-8"))

    jobs = [(group, tid, r) for group, items in expected.items() for tid in items for r in range(args.runs)]

    def work(job):
        group, tid, r = job
        text, _ = run_chat.judge(tasks[tid], replies[group][tid], args.judge_backend, args.judge_model)
        return job, score(tasks[tid], text), text

    results, agree, total, flips = [], 0, 0, 0
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for (group, tid, r), g, raw in pool.map(work, jobs):
            exp_fail = set(expected[group][tid])
            for cid, v in g["components"].items():
                total += 1
                agree += int((v < 1) == (cid in exp_fail))
            ok = set(g["failed_checks"]) == exp_fail
            print(f"{'OK  ' if ok else 'MISS'} {group:12s} {tid:32s} run{r}: failed={g['failed_checks']} expected={sorted(exp_fail)}")
            results.append({"group": group, "task": tid, "run": r, "failed": g["failed_checks"],
                            "expected": sorted(exp_fail), "match": ok, "judge_raw": raw})
    by_key = {}
    for x in results:
        by_key.setdefault((x["group"], x["task"]), []).append(tuple(sorted(x["failed"])))
    flips = sum(1 for v in by_key.values() if len(set(v)) > 1)
    summary = {"judge": f"{args.judge_backend}:{args.judge_model}", "check_level_agreement": round(agree / total, 3),
               "checks_judged": total, "replies_fully_matched": sum(x["match"] for x in results),
               "replies_judged": len(results), "replies_with_flipping_verdicts": flips, "results": results}
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "judge_validation.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\ncheck-level agreement {agree}/{total} = {agree/total:.1%}; replies fully matched "
          f"{summary['replies_fully_matched']}/{len(results)}; replies whose verdict changed between runs: {flips}")


if __name__ == "__main__":
    main()
