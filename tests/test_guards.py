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
def test_token_cap_is_never_exceeded_and_report_still_written():
    # Reserve-before-send: a small cap forces wrap-up; total tokens must stay under it
    cap = 30_000
    model = ScriptedModel(TOOL_STEPS * 3 + [report], tokens_per_call="realistic")
    # Make every later step a report so whichever call is the wrap-up call writes it
    model.steps = TOOL_STEPS + [report] * 10
    out = run_analyst(WEEK, model=model, max_tokens=cap, strict_accounting=True)
    u = out["run"]["usage"]
    assert out["run"]["status"] == "ok", out["run"]
    assert u["guard_counted_tokens"] <= cap
    assert u["total_tokens"] <= cap
    assert all(a["input_tokens"] <= a["reserved_input_tokens"] for a in u["model_attempts"])


def test_wrap_up_blocks_tools_when_budget_runs_low():
    model = ScriptedModel(TOOL_STEPS + [report] * 10, tokens_per_call="realistic")
    out = run_analyst(WEEK, model=model, max_tokens=22_000, strict_accounting=True)
    u = out["run"]["usage"]
    assert u["wrap_up"] and u["token_cap_hit"]
    assert out["run"]["tool_calls"] < len(TOOL_STEPS)       # investigation stopped early
    assert u["total_tokens"] <= 22_000


def test_cap_too_small_for_any_report_stops_run_without_sending():
    model = ScriptedModel(TOOL_STEPS + [report], tokens_per_call="realistic")
    out = run_analyst(WEEK, model=model, max_tokens=500, strict_accounting=True)
    assert out["run"]["status"] == "error"
    assert model.stream_calls == 0                           # nothing was sent


# ----------------------------------------------------------------------------- attempts
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


def test_wall_clock_limit_forces_report():
    model = ScriptedModel(TOOL_STEPS + [report] * 5, tokens_per_call="realistic")
    out = run_analyst(WEEK, model=model, max_seconds=0, strict_accounting=True)
    assert out["run"]["status"] == "ok"
    assert out["run"]["tool_calls"] == 0 and out["run"]["usage"]["wrap_up"]


# ----------------------------------------------------------------------------- Athena
class FakeAthena:
    def __init__(self, start_error=None, bytes_scanned=1000):
        self.start_error, self.bytes_scanned, self.starts = start_error, bytes_scanned, 0

    def start_query_execution(self, **kwargs):
        self.starts += 1
        if self.start_error:
            raise self.start_error
        return {"QueryExecutionId": f"q{self.starts}"}

    def get_query_execution(self, QueryExecutionId):
        return {"QueryExecution": {"Status": {"State": "SUCCEEDED"},
                                   "Statistics": {"DataScannedInBytes": self.bytes_scanned,
                                                  "EngineExecutionTimeInMillis": 5}}}

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
