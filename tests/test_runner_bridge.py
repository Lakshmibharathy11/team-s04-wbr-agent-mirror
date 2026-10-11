"""Analyst captures through the shared bridge (agent.runner_bridge -> src.analyst_adapter).

Replay only: captures come from offline runs (scripted model, golden DuckDB) and are
round-tripped through JSON like a saved file. No model, no Athena.
Requires src/ (the runner, adapter and numeric verifier). It is NOT skipped if src/
is missing: the import fails, so a missing runner can never look like a pass.

    python -m pytest tests/test_runner_bridge.py -q
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from src.wbr_contract import ContractError                  # noqa: E402  (must import, never skip)
from agent.analyst import run_analyst                       # noqa: E402
from agent.replay import EXPECTED_TOOLS, capture, replay    # noqa: E402
from agent.testing import ScriptedModel                     # noqa: E402
from test_evaluation_graders import call, good, report      # noqa: E402
from tools import config                                    # noqa: E402
from evaluate_agent import evaluate                         # noqa: E402

KEYS = {s["id"].split("_")[0]: s for s in
        json.loads((ROOT / "tests" / "golden" / "answer_keys.json").read_text())["scenarios"]}


def saved(out):
    """Round-trip through JSON, as a saved run file would be."""
    return json.loads(json.dumps(out, default=str))


def offline_run(sid, steps=None):
    key = KEYS[sid]
    config.SIMULATE_FAILURE = key.get("run_with", {}).get("WBR_SIMULATE_FAILURE", "")
    try:
        return saved(run_analyst(key["week"], model=ScriptedModel(steps or good(key))))
    finally:
        config.SIMULATE_FAILURE = ""


def full_plan_steps(week):
    """One call of every expected tool, then a fact claim."""
    return [call("get_weekly_metric", metric="product_revenue", week=week),
            call("get_breakdown", metric="product_revenue", week=week, dimension="state"),
            call("get_delivery_signals", week=week, state="SP"),
            report(("Q1", "Revenue changed this week.", "fact", [0]))]


# ------------------------------------------------- 1. PARTIAL and limitations survive
def test_partial_draft_and_unavailable_survive_the_bridge():
    out = offline_run("S5")
    draft = out["report_draft"]
    assert draft["draft_status"] == "PARTIAL" and draft["unavailable"]
    res = replay(out, KEYS["S5"]["week"])
    assert res.status == "partial"
    kept = res.renderer_input["analyst_draft"]
    assert kept["draft_status"] == "PARTIAL"
    assert kept["unavailable"] == draft["unavailable"]
    assert kept["precheck_issues"] == draft["precheck_issues"]


def test_precheck_issue_alone_blocks_complete():
    week = KEYS["S1"]["week"]
    steps = full_plan_steps(week)[:-1] + [
        report(("Q1", "Revenue fell because orders fell.", "fact", [0]))]   # causal -> precheck issue
    out = offline_run("S1", steps)
    assert out["report_draft"]["precheck_issues"]
    res = replay(out, week)
    assert res.status == "partial"
    assert res.renderer_input["analyst_draft"]["precheck_issues"] == out["report_draft"]["precheck_issues"]


def test_failed_analyst_run_is_visible():
    out = {"run": {"status": "error", "error": "RuntimeError: Run stopped by guard"},
           "report_draft": None, "evidence_log": {}, "calls": [], "transcript": []}
    res = replay(out, KEYS["S1"]["week"])
    assert res.status == "partial" and not res.claims
    assert any(w.startswith("analyst run failed") for w in res.warnings)


def test_saved_evaluation_run_shape_is_accepted():
    # scripts/evaluate_agent.py saves run_status/run_error instead of run={...}
    summary = evaluate(1, ["S5"], model_factory=lambda key, t: ScriptedModel(good(key)),
                       stop_on_failure=False)
    trial = saved(summary)["results"][0]["runs"][0]
    assert "run" not in trial and capture(trial)["run"]["status"] == "ok"
    assert replay(trial, KEYS["S5"]["week"]).status == "partial"


# ------------------------------------------------- 2. omitted required tools are detected
def test_omitted_expected_tools_are_reported():
    out = offline_run("S1")                       # this run only calls get_breakdown
    res = replay(out, KEYS["S1"]["week"])
    missing = [w for w in res.warnings if w.startswith("missing expected tools")]
    assert missing and "get_weekly_metric" in missing[0] and "get_delivery_signals" in missing[0]
    assert res.status == "partial"


def test_full_coverage_reports_nothing_missing():
    week = KEYS["S1"]["week"]
    res = replay(offline_run("S1", full_plan_steps(week)), week)
    assert not [w for w in res.warnings if w.startswith(("missing expected", "unexpected"))]


def test_expected_tools_are_fixed_not_taken_from_the_run():
    assert EXPECTED_TOOLS == ("get_weekly_metric", "get_breakdown", "get_delivery_signals")
    with pytest.raises(ContractError):
        replay(offline_run("S1"), KEYS["S1"]["week"], expected_tool_names=())


# ------------------------------------------------- 3. repeated calls keep names and captures
def test_repeated_calls_keep_original_names_and_captures():
    out = offline_run("S1")                       # get_breakdown twice
    res = replay(out, KEYS["S1"]["week"])
    names = [r["tool_name"] for r in res.tool_results]
    assert names == [c["tool"] for c in out["calls"]] and names.count("get_breakdown") == 2
    for r in res.tool_results:
        assert r["source_envelope"] == out["evidence_log"][r["request_id"]]


def _sp_drop(out):
    """The SP group of the health_beauty state breakdown, from the capture."""
    env = next(e for e in out["evidence_log"].values()
               if e["tool_name"] == "get_breakdown" and e["parameters"].get("category") == "health_beauty")
    sp = next(g for g in env["data"]["groups"] if g["group"] == "SP")
    return env["request_id"], sp


def _with_claim(out, statement, rid):
    out = json.loads(json.dumps(out))
    out["report_draft"]["claims"] = [{
        "claim_id": "c1", "question_id": "Q3", "statement": statement, "claim_type": "fact",
        "confidence": "medium", "supporting_evidence": [rid],
        "causal_language_allowed": False, "requires_human_review": False}]
    return out


def test_numeric_verifier_reads_the_original_capture():
    out = offline_run("S1")
    rid, sp = _sp_drop(out)
    if sp["change_type"] != "pct" or not sp["previous"] or sp["change"] is None:
        pytest.fail("golden S1 capture has no usable SP percentage")   # never silently skip
    word = "fell" if sp["change"] < 0 else "rose"
    amount = abs(sp["change"])
    week = KEYS["S1"]["week"]

    right = replay(_with_claim(out, f"health_beauty revenue {word} {amount}% in SP.", rid), week)
    assert right.claims[0]["verifier_reason"].startswith("numeric percentage matches")

    wrong = replay(_with_claim(out, f"health_beauty revenue {word} {amount + 20}% in SP.", rid), week)
    assert wrong.removed_claims and "contradicts" in wrong.removed_claims[0]["verifier_reason"]


# ------------------------------------------------- 4. failed requests stay visible
@pytest.mark.parametrize("bad, fixed", [
    ({"category": "beauty"}, {"category": "health_beauty"}),           # different category
    ({"state": "XX"}, {"state": "SP"}),                                # different state
])
def test_failed_request_stays_visible_after_a_later_success(bad, fixed):
    week = KEYS["S1"]["week"]
    base = {"metric": "product_revenue", "week": week, "dimension": "state", "category": "toys"}
    if "state" in bad:
        base = {"metric": "product_revenue", "week": week, "dimension": "category"}
    steps = [call("get_breakdown", **{**base, **bad}),
             call("get_breakdown", **{**base, **fixed}),
             report(("Q3", "Revenue changed in one group.", "fact", [1]))]
    out = offline_run("S1", steps)
    errors = [c for c in out["calls"] if c["status"] == "error"]
    assert len(errors) == 1
    assert out["report_draft"]["draft_status"] == "PARTIAL" and out["report_draft"]["unavailable"]
    res = replay(out, week)
    failed = [r for r in res.tool_results if r["status"] == "error"]
    assert [r["request_id"] for r in failed] == [errors[0]["request_id"]]
    assert res.status == "partial"
    assert res.renderer_input["analyst_draft"]["unavailable"] == out["report_draft"]["unavailable"]


def test_failed_request_stays_visible_across_weeks():
    # The week is bound to the run, so a failure for one week cannot be "cleared" by any
    # call: here a real failure (simulated timeout) is followed by an unrelated success.
    week = KEYS["S5"]["week"]
    steps = [call("get_weekly_metric", metric="avg_review_score", week="2018-01-01"),
             call("get_weekly_metric", metric="product_revenue", week=week),
             report(("Q1", "Revenue changed this week.", "fact", [1]))]
    out = offline_run("S5", steps)
    failed = [c for c in out["calls"] if c["status"] == "error"]
    assert len(failed) == 1 and failed[0]["args"]["week"] == week
    res = replay(out, week)
    assert res.status == "partial" and [r for r in res.tool_results if r["status"] == "error"]
