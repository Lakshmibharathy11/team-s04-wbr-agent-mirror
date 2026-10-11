"""Print a saved evaluation run as a readable weekly business review (for the demo video).
Offline: reads runs/eval_*.json only. No model, no Athena, no cost.

    python scripts/show_run.py runs/eval_YYYYMMDD_HHMMSS.json            # every scenario
    python scripts/show_run.py runs/eval_YYYYMMDD_HHMMSS.json S5         # one scenario
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.replay import replay
from src.wbr_contract import ContractError

QUESTIONS = {"Q1": "Why did revenue change?", "Q2": "Why did review scores change?",
             "Q3": "Which product categories underperformed, and why?"}


def show(result, saved):
    run = result["runs"][0]
    d = run.get("report_draft") or {}
    try:
        verified = replay(run, result["week"])
        boundary_status = verified.status.upper()
    except ContractError:
        boundary_status = "REJECTED: EVIDENCE PERIOD/CONTRACT MISMATCH"
    print("=" * 100)
    print(f"{result['scenario']}  |  week {result['week']} vs {d.get('compare_week')}  |  "
          f"source commit {saved.get('commit')}  |  {saved.get('provider')} {saved.get('model')} on {saved.get('backend')}  "
          f"(SYNTHETIC test scenario)")
    print(f"Original scenario grader: {'PASS' if run.get('passed') else 'FAIL'}; "
          f"captured draft: {d.get('draft_status')}; current bridge: {boundary_status}")
    print("UNVERIFIED CAPTURED DRAFT — scenario grading does not certify claims.")
    print("-" * 100)
    print("Investigation (tool calls):")
    for c in run.get("calls", []):
        args = ", ".join(f"{k}={v}" for k, v in c["args"].items() if v and k != "week")
        flag = "FAILED" if c["status"] == "error" else c["status"]
        warn = f"  warnings: {', '.join(sorted(set(c['warnings'])))}" if c.get("warnings") else ""
        print(f"  {c['step']:>2}. {c['tool']}({args}) week={c['args'].get('week')} "
              f" -> {flag}  [{c['request_id']}]{warn}")
    print("-" * 100)
    if boundary_status.startswith("REJECTED"):
        print("Known failed capture: claims withheld because the verification boundary rejected it.")
        return
    for q, text in QUESTIONS.items():
        claims = [c for c in d.get("claims", []) if c["question_id"] == q]
        print(f"{q}. {text}")
        if not claims:
            print("    (no captured claim for this question)")
        for c in claims:
            review = "  NEEDS HUMAN REVIEW" if c["requires_human_review"] else ""
            print(f"    [{c['claim_type']}, confidence {c['confidence']}{review}]")
            print(f"    {c['statement']}")
            print(f"    evidence: {', '.join(c['supporting_evidence'])}")
    if d.get("unavailable"):
        print("Unavailable / could not be checked:")
        for u in d["unavailable"]:
            print(f"    - {u}")
    if d.get("precheck_issues"):
        print("Pre-check issues (for the verifier):")
        for i in d["precheck_issues"]:
            print(f"    - {i['claim_id']}: {i['issue']}")
    if d.get("actionable_questions"):
        print("Questions for the analyst:")
        for a in d["actionable_questions"]:
            print(f"    - {a}")
    u = run.get("usage", {})
    print(f"Cost evidence: {u.get('model_calls')} model calls, {u.get('input_tokens')}/{u.get('output_tokens')} tokens, "
          f"{u.get('athena_queries')} Athena queries")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    saved = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    want = sys.argv[2] if len(sys.argv) > 2 else None
    for result in saved["results"]:
        if want and result["scenario"].split("_")[0] != want:
            continue
        show(result, saved)


if __name__ == "__main__":
    main()
