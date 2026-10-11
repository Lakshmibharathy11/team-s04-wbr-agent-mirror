"""Check the graders before trusting them: a scripted 'good' analyst must pass every
scenario and a scripted 'bad' analyst must fail every scenario. No Bedrock needed.

    python -m pytest tests/test_evaluation_graders.py -q

Requires data/golden.duckdb and tests/golden/answer_keys.json.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from agent.testing import ScriptedModel, request_ids  # noqa: E402
from evaluate_agent import evaluate                   # noqa: E402


def call(tool, **kwargs):
    return {"tool": tool, "input": kwargs}


def report(*claims, questions=("What should the analyst check next?",), unavailable=()):
    """claims: (question_id, statement, claim_type, [indexes of request_ids to cite])"""
    def step(messages):
        ids = request_ids(messages)
        return {"report": {"claims": [{"question_id": q, "statement": s, "claim_type": t,
                                       "supporting_evidence": [ids[i] for i in idx]}
                                      for q, s, t, idx in claims],
                           "actionable_questions": list(questions), "unavailable": list(unavailable)}}
    return step


def good(key):
    w = key["week"]
    sid = key["id"]
    if sid.startswith("S1"):
        return [call("get_breakdown", metric="product_revenue", week=w, dimension="category"),
                call("get_breakdown", metric="product_revenue", week=w, dimension="state", category="health_beauty"),
                report(("Q3", "health_beauty revenue fell sharply, with SP accounting for most of the drop.",
                        "fact", [0, 1]))]
    if sid.startswith("S2"):
        return [call("get_weekly_metric", metric="avg_review_score", week=w, state="RJ"),
                call("get_delivery_signals", week=w, state="RJ"),
                report(("Q2", "Average review score fell in RJ.", "fact", [0]),
                       ("Q2", "In RJ, late deliveries rose in the same week reviews fell; "
                              "the data does not establish a cause.", "observed_pattern", [0, 1]))]
    if sid.startswith("S3"):
        return [call("get_breakdown", metric="product_revenue", week=w, dimension="state"),
                call("get_breakdown", metric="product_revenue", week=w, dimension="category", state="MG"),
                call("get_delivery_signals", week=w, state="MG"),
                report(("Q1", "MG revenue fell sharply and ranks first among states.", "fact", [0]),
                       ("Q1", "The MG drop is spread across categories with no delivery change; "
                              "unexplained, needs analyst review.", "unexplained", [1, 2]))]
    if sid.startswith("S5"):
        return [call("get_weekly_metric", metric="avg_review_score", week=w),
                call("get_weekly_metric", metric="product_revenue", week=w),
                report(("Q1", "Revenue was within the normal range.", "fact", [1]),
                       unavailable=["avg_review_score: tool failed"])]
    # S4 and S7: check the headline metric, which comes back partial
    return [call("get_weekly_metric", metric="product_revenue", week=w),
            report(("Q1", "Revenue numbers for this week are not final.", "fact", [0]))]


def bad(key):
    w = key["week"]
    sid = key["id"]
    if sid.startswith("S1"):   # wrong category
        return [call("get_breakdown", metric="product_revenue", week=w, dimension="category"),
                report(("Q3", "housewares revenue fell in RJ.", "fact", [0]))]
    if sid.startswith("S2"):   # causal claim
        return [call("get_weekly_metric", metric="avg_review_score", week=w, state="RJ"),
                report(("Q2", "Reviews fell in RJ because deliveries were late.", "fact", [0]))]
    if sid.startswith("S3"):   # invents a delivery pattern
        return [call("get_breakdown", metric="product_revenue", week=w, dimension="state"),
                report(("Q1", "MG revenue fell alongside slower deliveries.", "observed_pattern", [0]))]
    if sid.startswith("S5"):   # builds a claim on the failed call
        return [call("get_weekly_metric", metric="avg_review_score", week=w),
                report(("Q2", "Review score was stable.", "fact", [0]))]
    # S4 and S7: never checks the data, so the report claims COMPLETE
    return [report(("Q1", "Revenue looked fine.", "fact", []))]


def test_good_analyst_passes_every_scenario():
    summary = evaluate(1, [], model_factory=lambda key, t: ScriptedModel(good(key)), stop_on_failure=False)
    failed = [r["scenario"] for r in summary["results"] if r["passed"] != 1]
    assert not failed, failed


def test_bad_analyst_fails_every_scenario():
    summary = evaluate(1, [], model_factory=lambda key, t: ScriptedModel(bad(key)), stop_on_failure=False)
    passed = [r["scenario"] for r in summary["results"] if r["passed"] != 0]
    assert not passed, passed


if __name__ == "__main__":
    test_good_analyst_passes_every_scenario()
    print("PASS  good analyst passes every scenario\n")
    test_bad_analyst_fails_every_scenario()
    print("PASS  bad analyst fails every scenario")