"""Model-free tests of the cost and safety guards (no AWS, no Bedrock, no charges).

    python -m pytest tests/test_guards.py -q

A scripted model drives the real Strands loop on the golden DuckDB tables; a fake
Athena client stands in for AWS. Each test proves one guard stops a call BEFORE
it is sent, or stops the batch on an unsafe condition.
"""

import json
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.analyst import run_analyst                     # noqa: E402
from agent.testing import ScriptedModel, request_ids      # noqa: E402
from tools import backend, config                         # noqa: E402
from tools.budget import LEDGER, athena_reservation       # noqa: E402

config.DUCKDB_PATH = str(ROOT / "data" / "golden.duckdb")
KEYS = json.loads((ROOT / "tests" / "golden" / "answer_keys.json").read_text())
WEEK = next(s for s in KEYS["scenarios"] if s["id"] == "S1_category_drop")["week"]

TOOL_STEPS = [
    {"tool": "get_weekly_metric", "input": {"metric": "product_revenue", "week": WEEK}},
    {"tool": "get_breakdown", "input": {"metric": "product_revenue", "week": WEEK, "dimension": "category"}},
    {"tool": "get_breakdown", "input": {"metric": "product_revenue", "week": WEEK, "dimension": "state"}},
    {"tool": "get_delivery_signals", "input": {"week": WEEK}},
    {"tool": "get_weekly_metric", "input": {"metric": "orders", "week": WEEK}},
    {"tool": "get_weekly_metric", "input": {"metric": "avg_review_score", "week": WEEK}},
]


def report(messages):
    return {"report": {"claims": [{"question_id": "Q1", "statement": "Revenue changed.",
                                   "claim_type": "fact", "supporting_evidence": request_ids(messages)}],
                       "actionable_questions": [], "unavailable": []}}


# Each test starts from local settings (tests/conftest.py).


# ----------------------------------------------------------------------------- tokens
# Token tests use a simulated 40,000-token context window so the numbers stay small.
# Every model call reserves context + output = 42,000 tokens before it is sent.
CTX = 40_000


def test_token_cap_is_never_exceeded_and_report_still_written():
    model = ScriptedModel(TOOL_STEPS + [report] * 10, tokens_per_call="realistic")
    out = run_analyst(WEEK, model=model, max_tokens=150_000, context_tokens=CTX, strict_accounting=True)
    u = out["run"]["usage"]
    assert out["run"]["status"] == "ok", out["run"]
    assert u["total_tokens"] <= 150_000 and u["guard_counted_tokens"] <= 150_000
    assert all(a["reserved_input_tokens"] == CTX for a in u["model_attempts"])


def test_underestimated_input_cannot_overshoot_the_cap():
    # The request text is small, but the model reports 35,000 input tokens per call:
    # any estimate based on the text would be far too low. The full-context reservation
    # still keeps the run under its cap, and investigation stops in time for the report.
    model = ScriptedModel([TOOL_STEPS[0], report, report], tokens_per_call=35_000)
    out = run_analyst(WEEK, model=model, max_tokens=100_000, context_tokens=CTX, strict_accounting=True)
    u = out["run"]["usage"]
    assert out["run"]["status"] == "ok", out["run"]
    assert u["total_tokens"] <= 100_000
    assert model.stream_calls == 2                        # 1 investigation call + 1 report call
    assert u["wrap_up"] and out["run"]["tool_calls"] == 1


def test_call_that_could_not_fit_is_prevented_not_detected():
    # 35,000 tokens per call, 42,000 reserved per call, cap 100,000. After the
    # investigation call, the model ignores the request to write the report (it asks
    # for another tool). Used = 70,000; a third call would need 42,000 more, so it is
    # refused BEFORE sending. The run ends under the cap; nothing is billed past it.
    model = ScriptedModel(TOOL_STEPS * 2, tokens_per_call=35_000)
    out = run_analyst(WEEK, model=model, max_tokens=100_000, context_tokens=CTX, strict_accounting=True)
    u = out["run"]["usage"]
    assert out["run"]["status"] == "error"
    assert model.stream_calls == 2                        # the third call was never sent
    assert u["total_tokens"] == 70_000 <= 100_000
    assert "could not fit" in u["stop_reason"]


def test_wrap_up_blocks_tools_when_budget_runs_low():
    model = ScriptedModel(TOOL_STEPS + [report] * 10, tokens_per_call="realistic")
    out = run_analyst(WEEK, model=model, max_tokens=95_000, context_tokens=CTX, strict_accounting=True)
    u = out["run"]["usage"]
    assert u["wrap_up"] and u["token_cap_hit"]
    assert out["run"]["tool_calls"] < len(TOOL_STEPS)       # investigation stopped early
    assert u["total_tokens"] <= 95_000


def test_cap_too_small_for_any_call_sends_nothing():
    model = ScriptedModel(TOOL_STEPS + [report], tokens_per_call="realistic")
    out = run_analyst(WEEK, model=model, max_tokens=41_000, context_tokens=CTX, strict_accounting=True)
    assert out["run"]["status"] == "error"
    assert model.stream_calls == 0                           # nothing was sent


def test_reported_usage_above_reservation_halts_batch():
    # Cannot happen with Bedrock's own limits, but if usage ever exceeds the bound, stop
    model = ScriptedModel(TOOL_STEPS + [report] * 5, tokens_per_call=CTX + 1)
    LEDGER.reset(0.75)
    out = run_analyst(WEEK, model=model, max_tokens=300_000, context_tokens=CTX, strict_accounting=True)
    assert out["run"]["status"] == "error" and model.stream_calls == 1
    assert LEDGER.halted == "model usage exceeded its reservation"


def test_model_attempt_limit_is_finite():
    model = ScriptedModel(TOOL_STEPS * 5, tokens_per_call="realistic")
    out = run_analyst(WEEK, model=model, max_model_calls=3, strict_accounting=True)
    assert out["run"]["status"] == "error"
    assert model.stream_calls == 3
    assert "model attempt limit" in out["run"]["usage"]["stop_reason"]


def test_model_failure_is_not_retried_and_halts_batch():
    model = ScriptedModel([RuntimeError("ThrottlingException: slow down")], tokens_per_call="realistic")
    LEDGER.reset(0.75)
    out = run_analyst(WEEK, model=model, strict_accounting=True)
    assert out["run"]["status"] == "error"
    assert model.stream_calls == 1                           # no retry
    assert LEDGER.halted and "model call failed" in LEDGER.halted
    assert LEDGER.entries[0]["charged_usd"] == LEDGER.entries[0]["reserved_usd"]  # kept in full


def test_missing_usage_numbers_halts_batch():
    model = ScriptedModel(TOOL_STEPS + [report], tokens_per_call=0)   # reports zero usage
    LEDGER.reset(0.75)
    out = run_analyst(WEEK, model=model, strict_accounting=True)
    assert out["run"]["status"] == "error"
    assert model.stream_calls == 1
    assert LEDGER.halted == "missing model cost accounting"


def test_budget_ceiling_refuses_call_before_sending():
    model = ScriptedModel(TOOL_STEPS + [report], tokens_per_call="realistic")
    LEDGER.reset(0.000001)                                   # far below one call's reservation
    out = run_analyst(WEEK, model=model, strict_accounting=True)
    assert out["run"]["status"] == "error"
    assert model.stream_calls == 0
    assert LEDGER.halted.startswith("cap risk")


def test_soft_time_limit_forces_report():
    model = ScriptedModel(TOOL_STEPS + [report] * 5, tokens_per_call="realistic")
    out = run_analyst(WEEK, model=model, max_seconds=0, strict_accounting=True)
    assert out["run"]["status"] == "ok"
    assert out["run"]["tool_calls"] == 0 and out["run"]["usage"]["wrap_up"]


# ----------------------------------------------------------------------------- Athena
class FakeAthena:
    """Stand-in for Athena. state: terminal state to report; bytes_scanned=None omits the
    scan statistics (as a misbehaving response would)."""

    def __init__(self, start_error=None, bytes_scanned=1000, state="SUCCEEDED"):
        self.start_error, self.bytes_scanned, self.state, self.starts = start_error, bytes_scanned, state, 0

    def start_query_execution(self, **kwargs):
        self.starts += 1
        if self.start_error:
            raise self.start_error
        return {"QueryExecutionId": f"q{self.starts}"}

    def get_query_execution(self, QueryExecutionId):
        stats = {"EngineExecutionTimeInMillis": 5}
        if self.bytes_scanned is not None:
            stats["DataScannedInBytes"] = self.bytes_scanned
        return {"QueryExecution": {"Status": {"State": self.state, "StateChangeReason": "test"},
                                   "Statistics": stats}}

    def get_paginator(self, name):
        class P:
            def paginate(self, QueryExecutionId):
                yield {"ResultSet": {"ResultSetMetadata": {"ColumnInfo": [{"Name": "x", "Type": "bigint"}]},
                                     "Rows": [{"Data": [{"VarCharValue": "x"}]}, {"Data": [{"VarCharValue": "1"}]}]}}
        return P()


def use_fake(monkeypatch, fake):
    monkeypatch.setattr(backend, "_athena_clients", lambda: (fake, fake))
    config.BACKEND = "athena"


def test_athena_access_denied_is_not_retried_and_halts(monkeypatch):
    fake = FakeAthena(ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "StartQueryExecution"))
    use_fake(monkeypatch, fake)
    LEDGER.reset(0.75)
    with pytest.raises(backend.QueryError) as exc:
        backend.run_query("q", "SELECT 1", [])
    assert exc.value.error_type == "access_denied" and fake.starts == 1
    assert LEDGER.halted.startswith("athena access denied")
    with pytest.raises(backend.QueryError):                 # later queries refused, not sent
        backend.run_query("q", "SELECT 1", [])
    assert fake.starts == 1


def test_athena_uncertain_start_halts_and_keeps_reservation(monkeypatch):
    fake = FakeAthena(ReadTimeoutError(endpoint_url="https://athena"))
    use_fake(monkeypatch, fake)
    LEDGER.reset(0.75)
    with pytest.raises(backend.QueryError) as exc:
        backend.run_query("q", "SELECT 1", [])
    assert exc.value.error_type == "uncertain" and fake.starts == 1
    assert LEDGER.entries[0]["charged_usd"] == pytest.approx(athena_reservation())


def test_athena_attempt_cap_blocks_before_sending(monkeypatch):
    fake = FakeAthena()
    use_fake(monkeypatch, fake)
    config.MAX_ATHENA_QUERIES = 2
    backend.run_query("q", "SELECT 1", [])
    backend.run_query("q", "SELECT 1", [])
    with pytest.raises(backend.QueryError) as exc:
        backend.run_query("q", "SELECT 1", [])
    assert exc.value.error_type == "query_budget" and fake.starts == 2


def test_athena_reserves_full_cutoff_then_settles_actual(monkeypatch):
    fake = FakeAthena(bytes_scanned=50_000)
    use_fake(monkeypatch, fake)
    LEDGER.reset(0.75)
    backend.run_query("q", "SELECT 1", [])
    entry = LEDGER.entries[0]
    assert entry["reserved_usd"] == pytest.approx(athena_reservation())   # 100 MiB worst case
    assert entry["actual_usd"] < entry["reserved_usd"]                      # 10 MB minimum billed


@pytest.mark.parametrize("state", ["FAILED", "CANCELLED", "SUCCEEDED"])
def test_athena_missing_scan_stats_keeps_reservation_and_stops_everything(monkeypatch, state):
    fake = FakeAthena(bytes_scanned=None, state=state)
    use_fake(monkeypatch, fake)
    LEDGER.reset(0.75)
    with pytest.raises(backend.QueryError) as exc:
        backend.run_query("q", "SELECT 1", [])
    assert exc.value.error_type == "uncertain"
    assert fake.starts == 1                                      # not retried
    entry = LEDGER.entries[0]
    assert entry["charged_usd"] == pytest.approx(athena_reservation())   # full reservation kept
    assert LEDGER.halted
    with pytest.raises(backend.QueryError):                     # no later query is sent
        backend.run_query("q", "SELECT 1", [])
    assert fake.starts == 1
    model = ScriptedModel([report], tokens_per_call="realistic")  # and no model call is sent
    out = run_analyst(WEEK, model=model, strict_accounting=True)
    assert out["run"]["status"] == "error" and model.stream_calls == 0


def test_athena_failed_with_valid_stats_is_accounted_and_retried_once(monkeypatch):
    fake = FakeAthena(bytes_scanned=0, state="FAILED")
    use_fake(monkeypatch, fake)
    LEDGER.reset(0.75)
    with pytest.raises(backend.QueryError) as exc:
        backend.run_query("q", "SELECT 1", [])
    assert exc.value.error_type == "query_failed" and fake.starts == 2   # contract: retry once
    assert not LEDGER.halted
    assert all(e["settled"] and e["actual_usd"] is not None for e in LEDGER.entries)


def test_athena_scan_above_reserved_bound_halts(monkeypatch):
    from tools.budget import ATHENA_SCAN_CUTOFF_BYTES
    fake = FakeAthena(bytes_scanned=ATHENA_SCAN_CUTOFF_BYTES + 1)
    use_fake(monkeypatch, fake)
    LEDGER.reset(0.75)
    with pytest.raises(backend.QueryError) as exc:
        backend.run_query("q", "SELECT 1", [])
    assert exc.value.error_type == "uncertain" and fake.starts == 1
    assert "exceeds the reserved bound" in LEDGER.halted


def test_athena_missing_credentials_sends_nothing_and_halts(monkeypatch):
    def broken():
        raise RuntimeError("NoRegion: You must specify a region")
    monkeypatch.setattr(backend, "_athena_clients", broken)
    config.BACKEND = "athena"
    LEDGER.reset(0.75)
    with pytest.raises(backend.QueryError) as exc:
        backend.run_query("q", "SELECT 1", [])
    assert exc.value.error_type == "access_denied"
    assert LEDGER.entries == [] and backend.QUERY_LOG == []     # nothing reserved or sent
    assert LEDGER.halted.startswith("athena credentials unavailable")


def test_athena_start_client_has_no_sdk_retries():
    start, read = backend._athena_clients()
    assert start.meta.config.retries["total_max_attempts"] == 1


def test_bedrock_client_has_no_sdk_retries(monkeypatch):
    monkeypatch.setenv("WBR_MODEL_ID", "us.amazon.nova-micro-v1:0")
    from agent.analyst import default_model
    model = default_model()
    assert model.client.meta.config.retries["total_max_attempts"] == 1
    assert model.config.get("max_tokens") == 2000


# ----------------------------------------------------------------------------- batch
def test_scenario_selection_is_exact():
    sys.argv = ["x"]
    import scripts.evaluate_agent as ev
    assert [s["id"] for s in ev.select(["S1"])] == ["S1_category_drop"]
    with pytest.raises(SystemExit):
        ev.select(["S6"])                                   # not a graded scenario
    with pytest.raises(SystemExit):
        ev.select(["S1", "S1"])


def test_batch_stops_at_first_unexpected_failure():
    import scripts.evaluate_agent as ev
    made = []

    def factory(key, t):                                     # a model that writes a useless report
        made.append(key["id"])
        return ScriptedModel([report], tokens_per_call="realistic")
    out = ev.evaluate(1, ["S1", "S2", "S3"], model_factory=factory)
    assert made == ["S1_category_drop"]                      # S2 and S3 never started
    assert out["totals"]["runs"] == 1 and "unexpected scenario failure" in out["totals"]["stopped_early"]
    run = out["results"][0]["runs"][0]
    assert "evidence_log" in run and "usage" in run and "model_attempts" in run["usage"]


def test_tools_run_one_at_a_time(monkeypatch):
    import agent.analyst as analyst
    from strands.tools.executors import SequentialToolExecutor
    seen = {}
    real_agent = analyst.Agent

    def capture(*args, **kwargs):
        seen.update(kwargs)
        return real_agent(*args, **kwargs)
    monkeypatch.setattr(analyst, "Agent", capture)
    run_analyst(WEEK, model=ScriptedModel([TOOL_STEPS[0], report]))
    assert isinstance(seen["tool_executor"], SequentialToolExecutor)


def test_evidence_is_saved_after_every_run(tmp_path):
    import scripts.evaluate_agent as ev
    path = tmp_path / "eval.json"
    ev.evaluate(1, ["S1", "S2"], save_path=path,
                model_factory=lambda key, t: ScriptedModel([report], tokens_per_call="realistic"))
    saved = json.loads(path.read_text())
    assert saved["totals"]["runs"] == 1                      # S1 saved before the batch stopped
    assert "evidence_log" in saved["results"][0]["runs"][0]
