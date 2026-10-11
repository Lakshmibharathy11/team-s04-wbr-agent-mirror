"""Regression tests for code-assigned confidence (agent/postchecks.py).

Confidence for a breakdown must come from the group the claim NAMES, not from the
first (z-ranked) group, because the agent picks groups by absolute_change or share.

    python -m pytest tests/test_postchecks.py -q
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.postchecks import _confidence, finalize   # noqa: E402
from tools.wbr_tools import get_breakdown            # noqa: E402

KEYS = {s["id"].split("_")[0]: s for s in
        json.loads((ROOT / "tests" / "golden" / "answer_keys.json").read_text())["scenarios"]}


def breakdown(groups, status="ok", warnings=()):
    return {"tool_name": "get_breakdown", "status": status,
            "warnings": [{"code": w} for w in warnings],
            "data": {"metric": "product_revenue", "dimension": "state", "groups": groups}}


# groups[0] is the z-ranked top (SP); the claim is about RJ
GROUPS = [{"group": "SP", "z_score": 5.0, "warnings": []},
          {"group": "RJ", "z_score": 1.2, "warnings": ["SMALL_SAMPLE"]},
          {"group": "MG", "z_score": 3.5, "warnings": []}]


def test_weak_named_group_gives_low_even_if_top_group_is_strong():
    assert _confidence([breakdown(GROUPS)], "Revenue in RJ fell.") == "low"


def test_named_group_z_decides_not_top_group():
    groups = [dict(GROUPS[0]), {"group": "RJ", "z_score": 1.2, "warnings": []}]
    assert _confidence([breakdown(groups)], "Revenue in RJ fell.") == "medium"   # old code said high


def test_strong_named_group_gives_high_even_if_not_top():
    assert _confidence([breakdown(GROUPS)], "Revenue in MG fell by the largest amount.") == "high"


def test_claim_naming_no_group_is_low():
    assert _confidence([breakdown(GROUPS)], "Revenue fell in one state.") == "low"


def test_names_match_whole_words_only():
    # "SPend" or "MGx" must not count as naming SP / MG
    assert _confidence([breakdown(GROUPS)], "SPending rose in MGx.") == "low"


def test_several_named_groups_use_the_weakest():
    groups = [{"group": "SP", "z_score": 5.0, "warnings": []},
              {"group": "MG", "z_score": 1.0, "warnings": []}]
    assert _confidence([breakdown(groups)], "SP and MG both fell.") == "medium"


def test_finalize_passes_the_statement():
    env = breakdown(GROUPS)
    ctx = SimpleNamespace(evidence_log={"r1": env}, calls=[])
    claim = SimpleNamespace(question_id="Q1", statement="Revenue in RJ fell.", claim_type="fact",
                            supporting_evidence=["r1"])
    draft = SimpleNamespace(claims=[claim], unavailable=[], actionable_questions=[])
    out = finalize(draft, ctx)["claims"][0]
    assert out["confidence"] == "low" and out["requires_human_review"] is True


def test_golden_s2_review_state_confidence_uses_rj():
    # Real golden data: the review breakdown for S2; the claim names RJ
    env = get_breakdown("avg_review_score", KEYS["S2"]["week"], "state")
    rj = next(g for g in env["data"]["groups"] if g["group"] == "RJ")
    expected = ("low" if set(rj["warnings"]) & {"SMALL_SAMPLE", "INSUFFICIENT_HISTORY", "INVALID_DATA",
                                                   "NO_DATA", "INCOMPLETE_WEEK", "ZERO_DENOMINATOR"}
                else "high" if abs(rj["z_score"] or 0) >= 3 else "medium")
    assert _confidence([env], "Review scores in RJ fell.") == expected


# ---------------------------------------------------------------- name forms
def test_category_written_with_spaces_or_ampersand_counts():
    groups = [{"group": "health_beauty", "z_score": 4.0, "warnings": []}]
    env = {**breakdown(groups), "data": {"groups": groups}}
    for text in ("health_beauty fell.", "Health & Beauty fell.", "health beauty fell."):
        assert _confidence([env], text) == "high", text


def test_state_full_name_counts():
    assert _confidence([breakdown(GROUPS)], "Revenue in Minas Gerais fell.") == "high"


def test_lowercase_other_is_not_the_other_group():
    groups = [{"group": "SP", "z_score": 4.0, "warnings": []},
              {"group": "Other", "z_score": 0.5, "warnings": []}]
    assert _confidence([breakdown(groups)], "SP fell; other states were flat.") == "high"
    assert _confidence([breakdown(groups)], "SP and Other fell.") == "medium"


# ---------------------------------------------------------------- unavailable / PARTIAL
def _call(step, tool, args, status, request_id):
    return {"step": step, "tool": tool, "args": args, "request_id": request_id,
            "status": status, "warnings": []}


def _finalize_with(calls, envs):
    ctx = SimpleNamespace(evidence_log=envs, calls=calls)
    draft = SimpleNamespace(claims=[], unavailable=[], actionable_questions=[])
    return finalize(draft, ctx)


def test_retried_validation_error_stays_unavailable():
    # Reviewer rule: a later success (even for a different state/category) never clears it
    bad = {"tool_name": "get_breakdown", "status": "error", "warnings": [],
           "error": {"type": "validation", "message": "Unknown category"}}
    good = breakdown(GROUPS)
    args = {"metric": "product_revenue", "dimension": "state", "category": "beauty"}
    for later in ({**args, "category": "health_beauty"}, {**args, "category": "toys"}):
        calls = [_call(1, "get_breakdown", args, "error", "r1"),
                 _call(2, "get_breakdown", later, "ok", "r2")]
        out = _finalize_with(calls, {"r1": bad, "r2": good})
        assert len(out["unavailable"]) == 1 and out["draft_status"] == "PARTIAL", later


def test_uncorrected_validation_error_is_unavailable():
    bad = {"tool_name": "get_breakdown", "status": "error", "warnings": [],
           "error": {"type": "validation", "message": "Unknown category"}}
    args = {"metric": "product_revenue", "dimension": "state", "category": "beauty"}
    out = _finalize_with([_call(1, "get_breakdown", args, "error", "r1")], {"r1": bad})
    assert out["unavailable"] and out["draft_status"] == "PARTIAL"


def test_data_error_always_unavailable_even_if_later_call_works():
    bad = {"tool_name": "get_weekly_metric", "status": "error", "warnings": [],
           "error": {"type": "timeout", "message": "Simulated failure"}}
    ok = {"tool_name": "get_weekly_metric", "status": "ok", "warnings": [], "data": {}}
    args = {"metric": "avg_review_score"}
    calls = [_call(1, "get_weekly_metric", args, "error", "r1"),
             _call(2, "get_weekly_metric", {**args, "state": "RJ"}, "ok", "r2")]
    out = _finalize_with(calls, {"r1": bad, "r2": ok})
    assert any("avg_review_score" in u for u in out["unavailable"]) and out["draft_status"] == "PARTIAL"


def test_validation_error_view_tells_the_model_to_retry():
    from agent.views import concise_view
    from tools.wbr_tools import get_weekly_metric
    view = concise_view(get_weekly_metric("not_a_metric", KEYS["S1"]["week"]))
    assert view["status"] == "error" and "call again" in view["guidance"]


# ---------------------------------------------------------------- reviewer's reproduction
def test_confidence_does_not_depend_on_group_order():
    # A fact claim about group B must not get A's statistics, whatever the list order
    a = {"group": "SP", "z_score": 6.0, "warnings": []}
    b = {"group": "RJ", "z_score": 0.4, "warnings": []}
    for groups in ([a, b], [b, a]):
        env = breakdown(groups)
        ctx = SimpleNamespace(evidence_log={"r1": env}, calls=[])
        claim = SimpleNamespace(question_id="Q1", statement="Revenue in RJ fell.", claim_type="fact",
                                supporting_evidence=["r1"])
        out = finalize(SimpleNamespace(claims=[claim], unavailable=[], actionable_questions=[]), ctx)
        c = out["claims"][0]
        assert c["confidence"] == "medium" and c["requires_human_review"] is False, groups


def test_claim_on_weak_group_needs_review_whatever_the_order():
    a = {"group": "SP", "z_score": 6.0, "warnings": []}
    b = {"group": "RJ", "z_score": 4.0, "warnings": ["SMALL_SAMPLE"]}
    for groups in ([a, b], [b, a]):
        ctx = SimpleNamespace(evidence_log={"r1": breakdown(groups)}, calls=[])
        claim = SimpleNamespace(question_id="Q1", statement="Revenue in RJ fell.", claim_type="fact",
                                supporting_evidence=["r1"])
        c = finalize(SimpleNamespace(claims=[claim], unavailable=[], actionable_questions=[]), ctx)["claims"][0]
        assert c["confidence"] == "low" and c["requires_human_review"] is True, groups


def test_prompt_treats_null_delivery_signal_as_unknown():
    from agent.prompts import SYSTEM_PROMPT
    assert "is_unusual null means\n  UNKNOWN" in SYSTEM_PROMPT
    assert "only when every checked signal has\n  is_unusual false" in SYSTEM_PROMPT
