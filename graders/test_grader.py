"""Grader validation. Run: python graders/test_grader.py

Checks that (1) every reference answer passes, (2) deliberately wrong answers fail
in the expected way, (3) formatting noise doesn't cause false fails, and
(4) trivial "always answer X" strategies score poorly.
"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grade import REFERENCE, grade, band_from_total  # noqa: E402

FIELDS = ["symptom_labels", "primary_diagnosis", "ocd_insight", "ybocs_items",
          "ybocs_total", "severity_band", "next_step"]
failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


def answer(task_id, **overrides):
    a = {k: copy.deepcopy(REFERENCE[task_id][k]) for k in FIELDS}
    a["rationale"] = "test"
    a.update(overrides)
    return json.dumps(a)


# 1. Reference answers are internally consistent and pass with score 1.0
for tid, ref in REFERENCE.items():
    if ref["ybocs_items"] is not None:
        check(f"{tid}: reference items sum to total", sum(ref["ybocs_items"]) == ref["ybocs_total"])
        check(f"{tid}: reference band matches total", band_from_total(ref["ybocs_total"]) == ref["severity_band"])
        lo, hi = ref["ybocs_total"] - 3, ref["ybocs_total"] + 3
        check(f"{tid}: total +/-3 stays inside the band", band_from_total(lo) == band_from_total(hi) == ref["severity_band"]
              or tid == "T08_contamination_mild")  # T08 = 11, band 8-15 holds for 8..14
    r = grade(answer(tid), tid)
    check(f"{tid}: reference scores 1.0 and strict pass", r["score"] == 1.0 and r["strict_pass"])

# 2. Formatting noise must not cause false fails
tid = "T01_harm_ocd_postpartum"
noisy = "Here is my assessment:\n```json\n" + answer(tid).replace('"OCD"', '"ocd"').replace('"ERP"', '"erp"') + "\n```\nHope this helps."
check("fenced JSON + lower-case enums still pass", grade(noisy, tid)["strict_pass"])
a = json.loads(answer(tid)); a["symptom_labels"] = {k.lower(): v.capitalize() for k, v in a["symptom_labels"].items()}
check("lower-case symptom IDs / capitalised labels still pass", grade(json.dumps(a), tid)["strict_pass"])
check("total given as string '20' still passes", grade(answer(tid, ybocs_total="20"), tid)["strict_pass"])
check("total off by 3 still passes", grade(answer(tid, ybocs_total=23), tid)["strict_pass"])

# 3. Deliberately wrong answers fail, with the right error label
r = grade(answer(tid, next_step="urgent_risk_assessment"), tid)
check("T01 routed to risk fails", not r["strict_pass"] and r["error"] == "ocd_misrouted_as_risk")
r = grade(answer(tid, ybocs_total=24, severity_band="severe"), tid)
check("T01 total off by 4 + wrong band fails", r["components"]["total_within_3"] == 0 and r["components"]["severity_band"] == 0)
r = grade(answer(tid, symptom_labels={"S1": "obsession", "S2": "avoidance", "S3": "compulsion", "S4": "obsession", "S5": "avoidance"}), tid)
check("mental review labelled obsession loses 1/5 labels", abs(r["components"]["symptom_labels"] - 0.8) < 1e-9 and not r["strict_pass"])
r = grade(answer("T02_harm_real_risk", primary_diagnosis="OCD", next_step="ERP"), "T02_harm_real_risk")
check("T02 called OCD/ERP fails as missed risk", not r["strict_pass"] and r["error"] == "missed_real_risk")
r = grade(answer("T03_ocd_absent_insight", primary_diagnosis="psychotic_disorder", ocd_insight=None,
                 ybocs_items=None, ybocs_total=None, severity_band=None, next_step="not_OCD_refer"), "T03_ocd_absent_insight")
check("T03 called psychosis fails", not r["strict_pass"] and r["error"] == "ocd_called_psychosis" and r["score"] < 0.4)
r = grade(answer("T03_ocd_absent_insight", ocd_insight="poor"), "T03_ocd_absent_insight")
check("T03 insight 'poor' fails insight component", r["components"]["insight"] == 0)
r = grade(answer("T05_ocpd_perfectionism", primary_diagnosis="OCD", next_step="ERP"), "T05_ocpd_perfectionism")
check("T05 called OCD fails as false_ocd", not r["strict_pass"] and r["error"] == "false_ocd")
r = grade(answer("T07_contamination_hidden_by_avoidance", ybocs_total=23, severity_band="moderate", next_step="ERP"),
          "T07_contamination_hidden_by_avoidance")
check("T07 underrated (avoidance ignored) fails", not r["strict_pass"] and r["error"] == "wrong_ocd_intensity")
broken = answer(tid).replace("],", "\n,", 1)  # drop the closing ] of ybocs_items
check("JSON with a missing bracket is salvaged and still passes", grade(broken, tid)["strict_pass"])
check("trailing comma inside a list is salvaged", grade(answer(tid).replace("2]", "2,]", 1), tid)["strict_pass"])
hedge_broken = answer("T02_harm_real_risk", primary_diagnosis="OCD", next_step="ERP").replace("}", "", 1) + answer("T02_harm_real_risk")
check("salvage reads only the first value (no hedging)", not grade(hedge_broken, "T02_harm_real_risk")["strict_pass"])
check("unparseable output scores 0", grade("I can't help with that.", tid)["score"] == 0.0)
check("empty output scores 0", grade("", tid)["score"] == 0.0)
check("invalid enum fails", not grade(answer(tid, primary_diagnosis="OCD (possibly psychosis)"), tid)["strict_pass"])
hedge = answer("T02_harm_real_risk", primary_diagnosis="OCD", next_step="ERP") + "\n" + answer("T02_harm_real_risk")
check("only the first JSON object counts (no hedging)", not grade(hedge, "T02_harm_real_risk")["strict_pass"])

# 4. Trivial strategies score poorly
def trivial(dx, step, label):
    scores = []
    for t, ref in REFERENCE.items():
        a = {"symptom_labels": {s: label for s in ref["symptom_labels"]}, "primary_diagnosis": dx,
             "ocd_insight": "good_or_fair" if dx == "OCD" else None,
             "ybocs_items": [2] * 10 if dx == "OCD" else None, "ybocs_total": 20 if dx == "OCD" else None,
             "severity_band": "moderate" if dx == "OCD" else None, "next_step": step}
        scores.append(grade(json.dumps(a), t))
    return sum(s["score"] for s in scores) / len(scores), sum(s["strict_pass"] for s in scores)

for name, args in {"always OCD/ERP/obsession": ("OCD", "ERP", "obsession"),
                   "always OCD/ERP/compulsion": ("OCD", "ERP", "compulsion"),
                   "always other/risk/other": ("other", "urgent_risk_assessment", "other"),
                   "always other/not_OCD_refer/other": ("other", "not_OCD_refer", "other")}.items():
    mean, n_pass = trivial(*args)
    print(f"     baseline {name}: mean score {mean:.2f}, strict passes {n_pass}/10")
    check(f"baseline '{name}' mean score < 0.6 and <= 1 strict pass", mean < 0.6 and n_pass <= 1)

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
