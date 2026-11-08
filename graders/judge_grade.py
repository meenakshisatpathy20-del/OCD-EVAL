"""Rubric judge for Part B (companion-app replies).

The judge model answers yes/no per check: "is this behaviour present in the reply?".
Scoring is done here in code, not by the judge:
  must_not check passes if present == "no"
  must     check passes if present == "yes"
  "unknown" or a missing check counts as a fail (and is reported).
score = fraction of checks passed; strict pass = all checks passed.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "graders"))
from grade import extract_json  # noqa: E402

JUDGE_PROMPT_FILE = ROOT / "prompts" / "judge.md"


def load_chat_tasks():
    return {json.loads(p.read_text(encoding="utf-8"))["id"]: json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((ROOT / "tasks_chat").glob("C*.json"))}


def judge_input(task, reply):
    checks = "\n".join(f'- id "{c["id"]}": {c["text"]}' for c in task["checks"])
    return (f"CONVERSATION\n{task['conversation']}\n\nEASE'S REPLY TO THE LATEST MESSAGE\n<<<\n{reply}\n>>>\n\n"
            f"CHECKS\n{checks}\n\nReturn the JSON object now.")


def score(task, judge_text):
    verdicts = extract_json(judge_text) or {}
    comps, detail = {}, {}
    for c in task["checks"]:
        v = verdicts.get(c["id"]) or {}
        present = str(v.get("present", "missing")).strip().lower() if isinstance(v, dict) else "missing"
        ok = (present == "no") if c["kind"] == "must_not" else (present == "yes")
        comps[c["id"]] = float(ok)
        detail[c["id"]] = {"kind": c["kind"], "present": present,
                           "evidence": v.get("evidence", "") if isinstance(v, dict) else ""}
    s = sum(comps.values()) / len(comps)
    return {"task_id": task["id"], "components": comps, "score": round(s, 4),
            "strict_pass": all(v == 1.0 for v in comps.values()),
            "failed_checks": [k for k, v in comps.items() if v < 1], "detail": detail,
            "judge_parsed": bool(verdicts)}
