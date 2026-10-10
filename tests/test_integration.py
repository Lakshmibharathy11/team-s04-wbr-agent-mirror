"""The analyst's output through Franklyn's runner (src/wbr_runner.py), via agent/integration.py.
Scripted model and golden DuckDB: no AWS, no Bedrock.

    python -m pytest tests/test_integration.py -q
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

wbr_runner = pytest.importorskip("src.wbr_runner")   # skipped if the runner is not in this checkout

from agent.analyst import run_analyst                       # noqa: E402
from agent.integration import acceptance_fixture, runner_inputs   # noqa: E402
from agent.testing import ScriptedModel                     # noqa: E402
from test_evaluation_graders import call, good, report      # noqa: E402
from tools import backend, config                           # noqa: E402

KEYS = {s["id"].split("_")[0]: s for s in
        json.loads((ROOT / "tests" / "golden" / "answer_keys.json").read_text())["scenarios"]}


def keep_if_cited(claim, evidence_by_ref):
    """Stand-in verifier (like Franklyn's test verifier): keep when all cited evidence exists."""
    ok = claim["supporting_evidence"] and set(claim["supporting_evidence"]) <= set(evidence_by_ref)
    return {"claim_id": claim["claim_id"], "outcome": "keep" if ok else "remove", "reason": "test"}


def run(sid, steps=None):
    key = KEYS[sid]
    config.SIMULATE_FAILURE = key.get("run_with", {}).get("WBR_SIMULATE_FAILURE", "")
    try:
        return run_analyst(key["week"], model=ScriptedModel(steps or good(key)))
    finally:
        config.SIMULATE_FAILURE = ""


def through_runner(out, verifier=keep_if_cited):
    tool_calls, analyst, names, excluded = runner_inputs(out)
    return wbr_runner.run_wbr(tool_calls, analyst, verifier=verifier, expected_tool_names=names), excluded


def test_s1_envelopes_and_claims_pass_the_runner_contract():
    result, _ = through_runner(run("S1"))
    assert not any("rejected" in w or "malformed" in w or "mismatch" in w for w in result.warnings)
    assert result.status == "complete" and not result.removed_claims
    # every claim's request_ids resolve in the runner's evidence index
    assert all(c["verifier_outcome"] == "keep" for c in result.claims)


def test_repeated_tool_gets_unique_names():
    out = run("S1")
    _, _, names, _ = runner_inputs(out)
    assert len(names) == len(set(names)) and all(n.startswith("get_breakdown") for n in names)


def test_failed_tool_is_partial_and_replay_sends_no_new_query():
    out = run("S5")
    before = len(backend.QUERY_LOG)
    result, _ = through_runner(out)
    assert len(backend.QUERY_LOG) == before          # the runner's retry replays, never re-queries
    assert result.status == "partial"
    failed = [r for r in result.tool_results if r["status"] == "error"]
    assert failed and "avg_review_score" in failed[0]["tool_name"] and failed[0]["error"]


def test_causal_claim_is_flagged_by_the_default_verifier():
    key = KEYS["S2"]
    steps = [call("get_weekly_metric", metric="avg_review_score", week=key["week"], state="RJ"),
             report(("Q2", "Reviews fell in RJ because deliveries were late.", "fact", [0]))]
    result, _ = through_runner(run("S2", steps), verifier=None)
    assert result.claims[0]["verifier_outcome"] == "flag"
    assert result.claims[0]["verifier_reason"] == "unsupported causal language"


def test_corrected_validation_error_is_excluded_and_listed():
    w = KEYS["S1"]["week"]
    steps = [call("get_breakdown", metric="product_revenue", week=w, dimension="category"),
             call("get_breakdown", metric="product_revenue", week=w, dimension="state", category="beauty"),
             call("get_breakdown", metric="product_revenue", week=w, dimension="state", category="health_beauty"),
             report(("Q3", "health_beauty revenue fell, mostly in SP.", "fact", [0, 2]))]
    result, excluded = through_runner(run("S1", steps))
    assert len(excluded) == 1 and excluded[0]["args"]["category"] == "beauty"
    assert result.status == "complete"


def test_acceptance_fixture_shape():
    fx = acceptance_fixture(run("S1"), "PLANTED_REAL_OLIST")
    assert set(fx) >= {"label", "expected_tool_names", "tool_results", "claims", "answer_key"}
    assert [r["tool_name"] for r in fx["tool_results"]] == fx["expected_tool_names"]
    json.dumps(fx)   # must be writable as JSON
