"""Offline tests of the analyst loop: a scripted model drives the real Strands loop,
real tools, and the golden database. No AWS or Bedrock needed.

    python -m pytest tests/test_agent_offline.py -q      (or: python tests/test_agent_offline.py)

Requires data/golden.duckdb (run scripts/build_golden_dataset.py first).
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.analyst import run_analyst            # noqa: E402
from agent.testing import ScriptedModel, request_ids  # noqa: E402
from tools import config                          # noqa: E402

config.DUCKDB_PATH = str(ROOT / "data" / "golden.duckdb")
KEYS = json.loads((ROOT / "tests" / "golden" / "answer_keys.json").read_text())
S1 = next(s for s in KEYS["scenarios"] if s["id"] == "S1_category_drop")
WEEK = S1["week"]


def cite_all(claims_text, claim_type="fact", unavailable=()):
    """A report step that cites every request_id seen so far."""
    def step(messages):
        ids = request_ids(messages)
        return {"report": {"claims": [{"question_id": "Q3", "statement": claims_text,
                                       "claim_type": claim_type, "supporting_evidence": ids}],
                           "actionable_questions": ["What happened to health_beauty in SP?"],
                           "unavailable": list(unavailable)}}
    return step


def test_investigation_path_and_report():
    steps = [
        {"tool": "get_weekly_metric", "input": {"metric": "product_revenue", "week": WEEK}},
        {"tool": "get_breakdown", "input": {"metric": "product_revenue", "week": WEEK, "dimension": "category"}},
        {"tool": "get_breakdown", "input": {"metric": "product_revenue", "week": WEEK, "dimension": "state",
                                            "category": "health_beauty"}},
        {"tool": "get_delivery_signals", "input": {"week": WEEK, "state": "SP", "category": "health_beauty"}},
        cite_all("health_beauty revenue fell, concentrated in SP."),
    ]
    out = run_analyst(WEEK, model=ScriptedModel(steps))
    assert out["run"]["status"] == "ok", out["run"]
    assert out["run"]["tool_calls"] == 4
    claim = out["report_draft"]["claims"][0]
    assert claim["claim_id"] == "c1" and claim["causal_language_allowed"] is False
    assert claim["confidence"] in ("low", "medium", "high")
    assert set(claim["supporting_evidence"]) <= set(out["evidence_log"])
    assert out["report_draft"]["precheck_issues"] == []
    assert [c["tool"] for c in out["calls"]] == ["get_weekly_metric", "get_breakdown",
                                                 "get_breakdown", "get_delivery_signals"]


def test_unknown_evidence_and_causal_language_are_flagged():
    steps = [
        {"tool": "get_weekly_metric", "input": {"metric": "product_revenue", "week": WEEK}},
        {"report": {"claims": [{"question_id": "Q1", "statement": "Revenue fell because reviews fell.",
                                "claim_type": "fact", "supporting_evidence": ["req-doesnotexist"]}],
                    "actionable_questions": [], "unavailable": []}},
    ]
    out = run_analyst(WEEK, model=ScriptedModel(steps))
    issues = {i["issue"] for i in out["report_draft"]["precheck_issues"]}
    assert {"unknown_evidence", "no_valid_evidence", "causal_language"} <= issues
    claim = out["report_draft"]["claims"][0]
    assert claim["confidence"] == "low" and claim["requires_human_review"] is True


def test_repeated_call_is_blocked():
    call = {"tool": "get_weekly_metric", "input": {"metric": "orders", "week": WEEK}}
    out = run_analyst(WEEK, model=ScriptedModel([call, call, cite_all("Orders checked.")]))
    assert out["run"]["tool_calls"] == 1
    assert [b["reason"] for b in out["run"]["blocked_calls"]] == ["duplicate"]


def test_tool_budget_is_enforced():
    metrics = ["orders", "items", "avg_order_value", "avg_item_price"]
    steps = [{"tool": "get_weekly_metric", "input": {"metric": m, "week": WEEK}} for m in metrics]
    out = run_analyst(WEEK, model=ScriptedModel(steps + [cite_all("Budget test.")]), max_tool_calls=2)
    assert out["run"]["tool_calls"] == 2
    assert [b["reason"] for b in out["run"]["blocked_calls"]] == ["budget", "budget"]


def test_tool_failure_makes_report_partial():
    config.SIMULATE_FAILURE = "avg_review_score"
    try:
        steps = [
            {"tool": "get_weekly_metric", "input": {"metric": "avg_review_score", "week": WEEK}},
            {"tool": "get_weekly_metric", "input": {"metric": "product_revenue", "week": WEEK}},
            cite_all("Revenue reported; review score unavailable."),
        ]
        out = run_analyst(WEEK, model=ScriptedModel(steps))
    finally:
        config.SIMULATE_FAILURE = ""
    report = out["report_draft"]
    assert report["draft_status"] == "PARTIAL"
    assert any("failed" in u for u in report["unavailable"])
    assert any(i["issue"] == "cites_failed_call" for i in report["precheck_issues"])


def test_invalid_week_returns_error():
    out = run_analyst("2018-06-05", model=ScriptedModel([]))
    assert out["run"]["status"] == "error" and out["report_draft"] is None


def test_model_failure_does_not_crash():
    class Broken(ScriptedModel):
        async def stream(self, *args, **kwargs):
            raise ConnectionError("model unavailable")
            yield  # pragma: no cover
    out = run_analyst(WEEK, model=Broken([]))
    assert out["run"]["status"] == "error" and "model unavailable" in out["run"]["error"]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")