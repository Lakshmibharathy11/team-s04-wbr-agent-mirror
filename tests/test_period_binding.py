"""Offline regression coverage: no model provider, AWS client or database calls."""
from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from agent.analyst import run_analyst
from agent.analyst_tools import make_tools
from agent.postchecks import finalize
from agent.replay import capture, replay
from agent.run_context import RunContext
from agent.testing import ScriptedModel, request_ids
from src.analyst_adapter import run_analyst_week
from tools import wbr_tools
from tools.envelope import make_envelope

WEEK = "2018-08-13"
OTHER_WEEK = "2018-08-20"
COMPARE = "2018-08-06"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Network forbidden in period regression tests")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def envelope(name="get_weekly_metric", week=WEEK, rid="r1", **args):
    params = {"week": week, "compare_week":
              (date.fromisoformat(week) - timedelta(days=7)).isoformat(), **args}
    data = {"metric": args.get("metric", "product_revenue"), "z_score": 1.0}
    if name == "get_breakdown":
        data.update(dimension=args["dimension"], groups=[])
    elif name == "get_delivery_signals":
        data = {"metrics": {}}
    return make_envelope(name, "ok", params, data=data, request_id=rid,
                         evidence_refs=[{"query_id": "offline", "backend": "synthetic",
                                         "table": "fixture", "row_count": 1,
                                         "executed_at": "2026-10-11T00:00:00Z"}])


def draft(ids=("r1",)):
    return SimpleNamespace(claims=[SimpleNamespace(
        question_id="Q1", statement="Revenue checked.", claim_type="fact",
        supporting_evidence=list(ids))], actionable_questions=[], unavailable=[])


@pytest.mark.parametrize("name,args", [
    ("get_weekly_metric", {"metric": "product_revenue"}),
    ("get_breakdown", {"metric": "product_revenue", "dimension": "category"}),
    ("get_delivery_signals", {"state": "SP"}),
])
def test_wrapper_schema_and_backend_are_bound_to_context(monkeypatch, name, args):
    seen = []
    def fake(**kw):
        seen.append(kw)
        return envelope(name, **kw)
    monkeypatch.setattr(wbr_tools, name, fake)
    ctx = RunContext(WEEK)
    wrapped = next(t for t in make_tools(ctx) if t.tool_name == name)
    # Compatible optional input; the wrapper records overrides and cannot execute it.
    assert "week" not in wrapped.tool_spec["inputSchema"]["json"].get("required", [])
    result = wrapped(**args)
    assert seen == [{**args, "state": args.get("state"),
                     "category": args.get("category"), "week": WEEK}]
    assert result["week"] == WEEK and ctx.calls[0]["args"]["week"] == WEEK


@pytest.mark.parametrize("field,value,issue", [
    ("week", OTHER_WEEK, "evidence_week_mismatch"),
    ("week", None, "evidence_week_mismatch"),
    ("compare_week", WEEK, "evidence_compare_week_mismatch"),
    ("compare_week", None, "evidence_compare_week_mismatch"),
])
def test_postcheck_flags_bad_cited_period_and_lowers_confidence(field, value, issue):
    env = envelope()
    env["parameters"][field] = value
    ctx = RunContext(WEEK)
    ctx.evidence_log["r1"] = env
    out = finalize(draft(), ctx)
    assert out["draft_status"] == "PARTIAL" and out["unavailable"]
    assert any(x["issue"] == issue for x in out["precheck_issues"])
    assert out["claims"][0]["confidence"] == "low"
    assert out["claims"][0]["requires_human_review"]


def test_uncited_wrong_period_still_blocks_complete():
    ctx = RunContext(WEEK)
    ctx.evidence_log = {"r1": envelope(), "r2": envelope(week=OTHER_WEEK, rid="r2")}
    assert finalize(draft(), ctx)["draft_status"] == "PARTIAL"


def test_correct_period_does_not_create_a_period_issue():
    ctx = RunContext(WEEK)
    ctx.evidence_log["r1"] = envelope()
    out = finalize(draft(), ctx)
    assert out["draft_status"] == "COMPLETE" and not out["precheck_issues"]


def test_scripted_wrong_weeks_execute_only_run_week_and_block_duplicate(monkeypatch):
    seen = []
    def fake(**args):
        seen.append(args)
        return envelope(rid=f"r{len(seen)}", **args)
    monkeypatch.setattr(wbr_tools, "get_weekly_metric", fake)
    def report(messages):
        return {"report": {"claims": [{"question_id": "Q1", "statement": "Revenue checked.",
                                       "claim_type": "fact", "supporting_evidence": request_ids(messages)}],
                           "actionable_questions": [], "unavailable": []}}
    steps = [{"tool": "get_weekly_metric", "input": {"metric": "product_revenue", "week": w}}
             for w in (OTHER_WEEK, WEEK)] + [report]
    out = run_analyst(WEEK, model=ScriptedModel(steps))
    assert out["run"]["status"] == "ok", out["run"]
    assert len(seen) == 1 and seen[0]["week"] == WEEK
    assert out["run"]["blocked_calls"][0]["reason"] == "duplicate"
    assert out["report_draft"]["week"] == WEEK


def captured(env):
    ctx = RunContext(WEEK)
    ctx.evidence_log["r1"] = env
    # Deliberately bypass finalize: the adapter must independently enforce periods.
    claim = {"claim_id": "c1", "question_id": "Q1", "statement": "Revenue checked.",
             "claim_type": "fact", "confidence": "medium", "supporting_evidence": ["r1"],
             "causal_language_allowed": False, "requires_human_review": False}
    return {"run": {"status": "ok"}, "evidence_log": ctx.evidence_log,
            "report_draft": {"week": WEEK, "compare_week": COMPARE, "claims": [claim],
                             "draft_status": "COMPLETE", "unavailable": [], "precheck_issues": []}}


def keep(claim, evidence):
    return {"claim_id": claim["claim_id"], "outcome": "keep", "reason": "test verifier"}


@pytest.mark.parametrize("field,value", [("week", OTHER_WEEK), ("week", None),
                                       ("compare_week", WEEK), ("compare_week", None)])
def test_replay_period_guard_precedes_even_a_permissive_verifier(field, value):
    env = envelope()
    env["parameters"][field] = value
    run = captured(env)
    before = deepcopy(run)
    result = run_analyst_week(WEEK, lambda _: run,
                             expected_tool_names=["get_weekly_metric"], verifier=keep)
    assert result.status == "partial" and not result.claims
    assert any("mismatch" in w for w in result.warnings)
    assert run == before


def test_replay_keeps_correct_period_under_explicit_test_verifier():
    result = replay(captured(envelope()), WEEK, expected_tool_names=["get_weekly_metric"], verifier=keep)
    assert result.status == "partial" and len(result.claims) == 1


def test_direct_adapter_rejects_inconsistent_draft_period():
    run = captured(envelope())
    run["report_draft"]["week"] = OTHER_WEEK
    result = run_analyst_week(WEEK, lambda _: run, expected_tool_names=["get_weekly_metric"], verifier=keep)
    assert result.status == "partial" and not result.claims


def test_real_failed_s1_capture_is_quarantined_without_rewriting_it():
    path = Path(__file__).resolve().parents[1] / "runs/eval_20261010_212231.json"
    batch = json.loads(path.read_text(encoding="utf-8-sig"))
    trial = batch["results"][0]["runs"][0]
    original = deepcopy(trial)
    from src.wbr_contract import ContractError
    with pytest.raises(ContractError, match="evidence week mismatch"):
        replay(trial, WEEK, verifier=keep)
    result = run_analyst_week(WEEK, lambda _: capture(trial),
                             expected_tool_names=["get_weekly_metric", "get_breakdown", "get_delivery_signals"],
                             verifier=keep)
    assert result.status == "partial" and not result.claims
    assert sum("evidence_week_mismatch" in w for w in result.warnings) == 12
    assert trial == original
