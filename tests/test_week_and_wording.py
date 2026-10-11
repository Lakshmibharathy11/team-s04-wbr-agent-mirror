"""Regressions from the af8fca9 live run (model passed week 2018-08-20 in a 2018-08-13
report) and from its claims ("may have contributed", "Other ... includes ... SP").

    python -m pytest tests/test_week_and_wording.py -q
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from src.wbr_contract import ContractError                         # noqa: E402
from agent.analyst import run_analyst                              # noqa: E402
from agent.postchecks import CAUSAL_PATTERN, finalize, group_relationship, wrong_week  # noqa: E402
from agent.replay import replay                                    # noqa: E402
from agent.testing import ScriptedModel                            # noqa: E402
from test_evaluation_graders import call, report                   # noqa: E402

KEYS = {s["id"].split("_")[0]: s for s in
        json.loads((ROOT / "tests" / "golden" / "answer_keys.json").read_text())["scenarios"]}
REPORT_WEEK, NEXT_WEEK, PREV_WEEK = "2018-08-13", "2018-08-20", "2018-08-06"


def env(week, compare, rid="r1"):
    return {"tool_name": "get_weekly_metric", "request_id": rid, "status": "ok", "warnings": [],
            "parameters": {"metric": "product_revenue", "week": week, "compare_week": compare,
                           "baseline_from": "2018-06-11"},
            "data": {"metric": "product_revenue", "z_score": 1.0}}


def finalize_one(statement, envelope, week=REPORT_WEEK):
    ctx = SimpleNamespace(evidence_log={envelope["request_id"]: envelope}, calls=[], week=week)
    claim = SimpleNamespace(question_id="Q1", statement=statement, claim_type="fact",
                            supporting_evidence=[envelope["request_id"]])
    out = finalize(SimpleNamespace(claims=[claim], unavailable=[], actionable_questions=[]), ctx)
    return out["claims"][0], out["precheck_issues"]


# ------------------------------------------------- week bound to the run
def test_model_cannot_choose_another_week():
    steps = [call("get_weekly_metric", metric="product_revenue", week=NEXT_WEEK),   # the live-run mistake
             call("get_breakdown", metric="product_revenue", week=NEXT_WEEK, dimension="state"),
             call("get_delivery_signals", week=NEXT_WEEK, state="SP"),
             report(("Q1", "Revenue changed this week.", "fact", [0]))]
    out = run_analyst(REPORT_WEEK, model=ScriptedModel(steps))
    assert out["run"]["status"] == "ok" and len(out["calls"]) == 3
    for c in out["calls"]:
        assert c["args"]["week"] == REPORT_WEEK and c["requested_week"] == NEXT_WEEK
    for e in out["evidence_log"].values():
        assert e["parameters"]["week"] == REPORT_WEEK and e["parameters"]["compare_week"] == PREV_WEEK
    assert not [i for i in out["report_draft"]["precheck_issues"] if i["issue"] == "evidence_wrong_week"]


def test_wrong_week_repeat_is_still_a_duplicate():
    steps = [call("get_weekly_metric", metric="product_revenue", week=REPORT_WEEK),
             call("get_weekly_metric", metric="product_revenue", week=NEXT_WEEK),
             report(("Q1", "Revenue changed this week.", "fact", [0]))]
    out = run_analyst(REPORT_WEEK, model=ScriptedModel(steps))
    assert len(out["calls"]) == 1 and out["run"]["blocked_calls"][0]["reason"] == "duplicate"


# ------------------------------------------------- evidence week checks
def test_postcheck_flags_evidence_for_another_week():
    claim, issues = finalize_one("Revenue fell this week.", env(NEXT_WEEK, REPORT_WEEK))
    assert {i["issue"] for i in issues} == {"evidence_wrong_week", "evidence_week_mismatch"}
    assert claim["requires_human_review"] is True


def test_previous_week_and_baseline_inside_a_result_are_valid():
    assert not wrong_week(env(REPORT_WEEK, PREV_WEEK), REPORT_WEEK)
    assert wrong_week(env(NEXT_WEEK, REPORT_WEEK), REPORT_WEEK)
    assert wrong_week(env(REPORT_WEEK, "2018-07-30"), REPORT_WEEK)    # wrong comparison week
    _, issues = finalize_one("Revenue fell this week.", env(REPORT_WEEK, PREV_WEEK))
    assert not issues


def test_replay_rejects_a_capture_with_evidence_for_another_week():
    week = KEYS["S1"]["week"]
    steps = [call("get_weekly_metric", metric="product_revenue", week=week),
             report(("Q1", "Revenue changed this week.", "fact", [0]))]
    out = json.loads(json.dumps(run_analyst(week, model=ScriptedModel(steps)), default=str))
    assert replay(out, week).status in ("partial", "complete")          # valid capture replays
    bad = json.loads(json.dumps(out))
    e = next(iter(bad["evidence_log"].values()))
    e["parameters"]["week"] = "2018-08-20" if week != "2018-08-20" else "2018-08-27"
    with pytest.raises(ContractError, match="evidence week mismatch"):
        replay(bad, week)


# ------------------------------------------------- tentative causal wording
@pytest.mark.parametrize("text", [
    "A delivery change may have contributed to the revenue drop.",
    "Delivery performance did not significantly impact review scores.",
    "Late deliveries likely lowered reviews.",
    "SP was the largest contributor to the decline.",
    "This could have affected revenue.",
    "Reviews fell owing to late deliveries.",
    "Late deliveries triggered the drop.",
    "Slower delivery may explain the drop.",
])
def test_tentative_causal_wording_is_flagged(text):
    assert CAUSAL_PATTERN.search(text)
    _, issues = finalize_one(text, env(REPORT_WEEK, PREV_WEEK))
    assert "causal_language" in [i["issue"] for i in issues]


@pytest.mark.parametrize("text", [
    "SP accounts for 46.28% of the total change.",
    "MG revenue fell across categories; unexplained, needs analyst review.",
    "MG revenue fell; the change is unexplained by the available signals.",
    "Late deliveries rose in RJ; at the same time, reviews fell. The data does not establish a cause.",
])
def test_plain_descriptions_are_not_flagged(text):
    assert not CAUSAL_PATTERN.search(text)


# ------------------------------------------------- group relationships
@pytest.mark.parametrize("text", [
    "The 'Other' state, which includes the 'SP' state, showed a decrease.",
    "SP is part of Other.",
    "Other categories contain health_beauty.",
])
def test_group_containment_is_flagged(text):
    assert group_relationship(text)
    _, issues = finalize_one(text, env(REPORT_WEEK, PREV_WEEK))
    assert "group_relationship" in [i["issue"] for i in issues]


@pytest.mark.parametrize("text", [
    "Within the auto category, SP had the most negative change.",     # category x state is valid
    "Revenue fell in several states, including SP and RJ.",
    "health_beauty revenue fell in SP.",
])
def test_valid_group_mentions_are_not_flagged(text):
    assert not group_relationship(text)
