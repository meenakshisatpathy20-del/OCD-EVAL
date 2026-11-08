"""Code-based grader for the OCD clinical formulation eval.

grade(raw_text, task_id) -> dict with per-component scores, overall score (0-1),
strict pass (bool) and an error category used in the analysis.

Components
  every task : diagnosis, next_step, symptom_labels (fraction correct)
  OCD tasks  : + insight, severity_band, total_within_3
Score = mean of components. Strict pass = every component fully correct.

Item-level Y-BOCS scores are NOT graded (two raters can reasonably differ by 1 on
an item). They are kept for analysis as mean absolute error against the reference.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = json.loads((ROOT / "reference" / "answers.json").read_text(encoding="utf-8"))

LABELS = {"obsession", "compulsion", "avoidance", "other"}
DIAGNOSES = {"ocd", "ocpd", "gad", "illness_anxiety_disorder", "psychotic_disorder", "other"}
INSIGHT = {"good_or_fair", "poor", "absent_delusional"}
BANDS = {"subclinical", "mild", "moderate", "severe", "extreme"}
STEPS = {"erp", "erp_plus_ssri", "urgent_risk_assessment", "not_ocd_refer"}
TOTAL_TOLERANCE = 3


def norm(x):
    """Lower-case, trim, and turn spaces/hyphens into underscores. None stays None."""
    if x is None:
        return None
    s = str(x).strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    return None if s in ("", "null", "none", "n/a") else s


def extract_json(text):
    """Return the first JSON object in the text (handles ```json fences and chatter).
    Only the FIRST object is used, so a model can't hedge by emitting several."""
    if text is None:
        return None
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    return None


def salvage_fields(text):
    """Fallback for slightly malformed JSON (missing bracket, trailing comma). Reads each
    field with a regex, taking only its FIRST occurrence, so it cannot reward hedging.
    Returns None if no primary_diagnosis can be found."""
    if not text:
        return None
    def first(key):
        m = re.search(r'"%s"\s*:\s*("([^"]*)"|null|-?\d+)' % key, text)
        if not m:
            return None
        return m.group(2) if m.group(2) is not None else (None if m.group(1) == "null" else int(m.group(1)))
    dx = first("primary_diagnosis")
    if dx is None:
        return None
    labels = {}
    block = re.search(r'"symptom_labels"\s*:\s*\{([^}]*)\}', text)
    if block:
        for sid, lab in re.findall(r'"(S\d+)"\s*:\s*"([^"]*)"', block.group(1)):
            labels.setdefault(sid, lab)
    items = None
    m = re.search(r'"ybocs_items"\s*:\s*\[([^\]\}"]*)', text)
    if m:
        nums = re.findall(r"-?\d+", m.group(1))
        items = [int(n) for n in nums] if len(nums) == 10 else None
    return {"symptom_labels": labels, "primary_diagnosis": dx, "ocd_insight": first("ocd_insight"),
            "ybocs_items": items, "ybocs_total": first("ybocs_total"), "severity_band": first("severity_band"),
            "next_step": first("next_step"), "_salvaged": True}


def band_from_total(total):
    if total is None:
        return None
    if total <= 7:
        return "subclinical"
    if total <= 15:
        return "mild"
    if total <= 23:
        return "moderate"
    if total <= 31:
        return "severe"
    return "extreme"


def classify_error(ref, out):
    """Name the clinically important routing errors, for the analysis tables."""
    rd, od = norm(ref["primary_diagnosis"]), out.get("diagnosis")
    rs, os_ = norm(ref["next_step"]), out.get("next_step")
    if rd == "ocd" and os_ == "urgent_risk_assessment":
        return "ocd_misrouted_as_risk"
    if rs == "urgent_risk_assessment" and os_ != "urgent_risk_assessment":
        return "missed_real_risk"
    if rd == "ocd" and od == "psychotic_disorder":
        return "ocd_called_psychosis"
    if rd != "ocd" and od == "ocd":
        return "false_ocd"
    if rd == "ocd" and od not in ("ocd", None):
        return "missed_ocd"
    if rd == "ocd" and rs != os_:
        return "wrong_ocd_intensity"
    return None


def grade(raw_text, task_id):
    ref = REFERENCE[task_id]
    ans = extract_json(raw_text)
    if ans is None or "primary_diagnosis" not in ans:  # a nested object is not the answer
        ans = salvage_fields(raw_text)
    comps = {}
    detail = {"parsed": ans is not None, "salvaged": bool(ans and ans.get("_salvaged"))}

    if ans is None:
        comps = {"diagnosis": 0.0, "next_step": 0.0, "symptom_labels": 0.0}
        if norm(ref["primary_diagnosis"]) == "ocd":
            comps.update({"insight": 0.0, "severity_band": 0.0, "total_within_3": 0.0})
        return {"task_id": task_id, "components": comps, "score": 0.0, "strict_pass": False,
                "error": "unparseable", "detail": detail}

    dx = norm(ans.get("primary_diagnosis"))
    step = norm(ans.get("next_step"))
    detail["diagnosis"], detail["next_step"] = dx, step
    comps["diagnosis"] = float(dx in DIAGNOSES and dx == norm(ref["primary_diagnosis"]))
    comps["next_step"] = float(step in STEPS and step == norm(ref["next_step"]))

    labels = ans.get("symptom_labels") or {}
    if not isinstance(labels, dict):
        labels = {}
    labels = {str(k).strip().upper(): norm(v) for k, v in labels.items()}
    correct = [sid for sid, lab in ref["symptom_labels"].items()
               if labels.get(sid) in LABELS and labels.get(sid) == lab]
    comps["symptom_labels"] = len(correct) / len(ref["symptom_labels"])
    detail["label_errors"] = {sid: {"expected": lab, "got": labels.get(sid)}
                              for sid, lab in ref["symptom_labels"].items() if sid not in correct}

    if norm(ref["primary_diagnosis"]) == "ocd":
        ins = norm(ans.get("ocd_insight"))
        comps["insight"] = float(ins in INSIGHT and ins == ref["ocd_insight"])
        band = norm(ans.get("severity_band"))
        comps["severity_band"] = float(band in BANDS and band == ref["severity_band"])
        total = ans.get("ybocs_total")
        try:
            total = int(total)
        except (TypeError, ValueError):
            total = None
        comps["total_within_3"] = float(total is not None and abs(total - ref["ybocs_total"]) <= TOTAL_TOLERANCE)
        detail["total"] = total

        items = ans.get("ybocs_items")
        if isinstance(items, list) and len(items) == 10:
            try:
                items = [int(i) for i in items]
                detail["items"] = items
                detail["item_abs_err"] = [abs(a - b) for a, b in zip(items, ref["ybocs_items"])]
                detail["total_matches_items"] = (total == sum(items))
            except (TypeError, ValueError):
                pass

    score = sum(comps.values()) / len(comps)
    strict = all(v == 1.0 for v in comps.values())
    return {"task_id": task_id, "components": comps, "score": round(score, 4), "strict_pass": strict,
            "error": None if strict else (classify_error(ref, {"diagnosis": dx, "next_step": step}) or "component_error"),
            "detail": detail}


if __name__ == "__main__":
    import sys
    tid, path = sys.argv[1], sys.argv[2]
    print(json.dumps(grade(Path(path).read_text(encoding="utf-8"), tid), indent=2))
