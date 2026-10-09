"""Shared test setup: every test starts from safe, local settings.

Tests must never depend on the terminal's environment. Without this, a terminal
that has WBR_BACKEND=athena or WBR_BATCH_BUDGET_USD set (as for an approved AWS
run) would make the offline tests try to reach AWS.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import backend, config      # noqa: E402
from tools.budget import LEDGER        # noqa: E402


@pytest.fixture(autouse=True)
def local_settings(monkeypatch):
    # Local DuckDB, no ledger, no caps, no simulated failures
    config.BACKEND, config.TABLE_PREFIX = "duckdb", ""
    config.MAX_ATHENA_QUERIES, config.SIMULATE_FAILURE = None, ""
    config.DUCKDB_PATH = str(ROOT / "data" / "golden.duckdb")
    LEDGER.reset(None)
    backend.QUERY_LOG.clear()
    # Fake, inert AWS credentials: client objects can be built, nothing can sign in
    for var in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-2")
    import boto3
    boto3.DEFAULT_SESSION = None
    yield
    LEDGER.reset(None)
    config.BACKEND, config.MAX_ATHENA_QUERIES, config.SIMULATE_FAILURE = "duckdb", None, ""
    backend.QUERY_LOG.clear()
    boto3.DEFAULT_SESSION = None
