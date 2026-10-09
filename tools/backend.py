"""Runs the fixed SQL templates on DuckDB (local) or Athena (AWS).

The tools call run_query() and never touch a database directly, so switching
backends is a single setting (WBR_BACKEND) and nothing else changes.
"""

import threading
import time
from datetime import date
from pathlib import Path

from tools import config
from tools.budget import (ATHENA_SCAN_CUTOFF_BYTES, LEDGER, S3_ALLOWANCE_PER_QUERY_USD,
                          BudgetError, athena_cost, athena_reservation)
from tools.envelope import now_iso

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# Every Athena attempt in this process (including retries and failures), so
# test runs can report query count, data scanned and run time.
QUERY_LOG: list[dict] = []


def athena_usage() -> dict:
    """Totals for the Athena attempts logged so far."""
    scanned = sum(q.get("bytes_scanned") or 0 for q in QUERY_LOG)
    return {
        "attempts": len(QUERY_LOG),
        "succeeded": sum(q["state"] == "SUCCEEDED" for q in QUERY_LOG),
        "bytes_scanned": scanned,
        # Athena bills at least 10 MB per query that scans data
        "billed_bytes_estimate": sum(max(q.get("bytes_scanned") or 0, 10 * 1024 * 1024)
                                     for q in QUERY_LOG if q["state"] == "SUCCEEDED"),
        "engine_ms": sum(q.get("engine_ms") or 0 for q in QUERY_LOG),
    }


NO_RETRY = {"query_budget", "access_denied", "uncertain"}


class QueryError(Exception):
    """A query that failed. error_type: 'timeout', 'query_failed', or (never retried)
    'query_budget', 'access_denied', 'uncertain'."""

    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


# ---------------------------------------------------------------------------
# DuckDB
# ---------------------------------------------------------------------------
def _duckdb_path() -> str:
    """Resolve the database path from the project root, so it works from any folder."""
    path = Path(config.DUCKDB_PATH)
    return str(path if path.is_absolute() else PROJECT_ROOT / path)


def _run_duckdb(sql: str, params: list) -> tuple[list[dict], dict]:
    import duckdb

    # read_only=True: the tools can never change the data (guardrail)
    con = duckdb.connect(_duckdb_path(), read_only=True)
    timer = threading.Timer(config.QUERY_TIMEOUT_SECONDS, con.interrupt)
    try:
        timer.start()
        cursor = con.execute(sql, params)
        columns = [col[0] for col in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    except duckdb.InterruptException:
        raise QueryError("timeout", f"Query exceeded {config.QUERY_TIMEOUT_SECONDS} seconds")
    except duckdb.Error as exc:
        raise QueryError("query_failed", str(exc))
    finally:
        timer.cancel()
        con.close()
    return rows, {}


# ---------------------------------------------------------------------------
# Athena (needs AWS credentials from the AWS CLI profile; not used in tests)
# ---------------------------------------------------------------------------
def _athena_literal(value) -> str:
    """Athena execution parameters are inserted as SQL literals, so text needs quotes.
    Values are already checked against allowed lists before reaching here."""
    if isinstance(value, date):  # a bare 2018-03-12 would be read as subtraction
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def _convert(value: str | None, athena_type: str):
    """Athena returns every value as text; convert it back using the column type."""
    if value is None:
        return None
    if athena_type in ("integer", "bigint", "smallint", "tinyint"):
        return int(value)
    if athena_type in ("double", "float", "real") or athena_type.startswith("decimal"):
        return float(value)
    if athena_type == "boolean":
        return value.lower() == "true"
    if athena_type == "date":  # DuckDB returns date objects; the tools look weeks up by date
        return date.fromisoformat(value)
    return value


def _athena_clients():
    """Two clients: one that starts queries with NO automatic SDK retries (so every
    start attempt is one we counted and reserved), and one for free status/result
    reads that may retry normally."""
    import boto3
    from botocore.config import Config
    timeouts = {"connect_timeout": 10, "read_timeout": 30}
    start = boto3.client("athena", region_name=config.AWS_REGION,
                         config=Config(retries={"total_max_attempts": 1}, **timeouts))
    read = boto3.client("athena", region_name=config.AWS_REGION,
                        config=Config(retries={"total_max_attempts": 3, "mode": "standard"}, **timeouts))
    return start, read


def _run_athena(sql: str, params: list) -> tuple[list[dict], dict]:
    from botocore.exceptions import BotoCoreError, ClientError

    # Guard 1: hard cap on start attempts (retries included)
    if config.MAX_ATHENA_QUERIES is not None and len(QUERY_LOG) >= config.MAX_ATHENA_QUERIES:
        raise QueryError("query_budget",
                         f"Athena query cap reached ({config.MAX_ATHENA_QUERIES}); query not sent")
    # Clients first: if credentials or region are missing, nothing can be sent
    try:
        start_client, read_client = _athena_clients()
    except Exception as exc:
        LEDGER.halt(f"athena credentials unavailable: {type(exc).__name__}")
        raise QueryError("access_denied", f"Cannot create Athena client ({type(exc).__name__}: {exc})")
    # Guard 2: reserve the worst-case cost (full 100 MiB scan + S3) before sending
    try:
        rid = LEDGER.reserve("athena", athena_reservation(), "start_query_execution")
    except BudgetError as exc:
        raise QueryError("query_budget", str(exc))
    log = {"execution_id": None, "state": "NOT_STARTED", "bytes_scanned": None, "engine_ms": None,
           "started_at": now_iso()}
    QUERY_LOG.append(log)
    request = {
        "QueryString": sql,
        "WorkGroup": config.ATHENA_WORKGROUP,
        "QueryExecutionContext": {"Database": config.ATHENA_DATABASE},
    }
    if params:
        request["ExecutionParameters"] = [_athena_literal(p) for p in params]

    # Start the query. Be precise about what we know happened.
    try:
        query_id = start_client.start_query_execution(**request)["QueryExecutionId"]
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        LEDGER.settle(rid, 0.0, f"rejected before running: {code}")   # nothing ran
        if "AccessDenied" in code or code in ("UnrecognizedClientException", "ExpiredTokenException"):
            log["state"] = "ACCESS_DENIED"
            LEDGER.halt(f"athena access denied: {code}")
            raise QueryError("access_denied", f"Athena access denied ({code}); batch halted")
        log["state"] = "REJECTED"
        raise QueryError("query_failed", str(exc))
    except BotoCoreError as exc:
        # e.g. a network timeout: the query may or may not have started
        log["state"] = "UNCERTAIN"
        LEDGER.settle(rid, None, "uncertain start; full reservation kept")
        LEDGER.halt("uncertain athena execution")
        raise QueryError("uncertain", f"Athena start uncertain ({type(exc).__name__}); batch halted")
    log["execution_id"] = query_id

    def valid_bytes(value) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def stop_batch(reason: str, message: str):
        """Keep the FULL reservation (cost unknown or above bound) and halt the batch."""
        LEDGER.settle(rid, None, f"{reason}; full reservation kept")
        LEDGER.halt(f"athena: {reason}")
        raise QueryError("uncertain", f"{message}; batch halted")

    try:
        # Wait for the query to finish
        deadline = time.monotonic() + config.QUERY_TIMEOUT_SECONDS
        while True:
            execution = read_client.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]
            status, state = execution["Status"], execution["Status"]["State"]
            stats = execution.get("Statistics", {})
            log.update(state=state, bytes_scanned=stats.get("DataScannedInBytes"),
                       engine_ms=stats.get("EngineExecutionTimeInMillis"),
                       total_ms=stats.get("TotalExecutionTimeInMillis"))
            if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
                break
            if time.monotonic() > deadline:
                # Stopped part-way: the billed amount is not known, so treat it as uncertain
                read_client.stop_query_execution(QueryExecutionId=query_id)
                log["state"] = "STOPPED_TIMEOUT"
                stop_batch("query timed out and was stopped",
                           f"Query exceeded {config.QUERY_TIMEOUT_SECONDS} seconds")
            time.sleep(0.5)

        # Terminal state: the scan statistics must be present, valid and within the bound
        scanned = log["bytes_scanned"]
        if not valid_bytes(scanned):
            stop_batch(f"{state} with missing or invalid scan statistics ({scanned!r})",
                       f"Athena {state} without valid scan statistics")
        actual = athena_cost(scanned) + S3_ALLOWANCE_PER_QUERY_USD
        if scanned > ATHENA_SCAN_CUTOFF_BYTES or actual > athena_reservation():
            stop_batch(f"reported scan {scanned} bytes exceeds the reserved bound",
                       "Athena reported more usage than was reserved")
        LEDGER.settle(rid, actual, state.lower())
        rid = None
        if state != "SUCCEEDED":
            # Known, accounted failure: may be retried once under the failure contract
            raise QueryError("query_failed", status.get("StateChangeReason", state))

        # Read all result pages; the first row of the first page is the header
        rows, columns, types = [], None, None
        for page_number, page in enumerate(
            read_client.get_paginator("get_query_results").paginate(QueryExecutionId=query_id)
        ):
            if columns is None:
                meta = page["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]
                columns = [c["Name"] for c in meta]
                types = [c["Type"].lower() for c in meta]
            data_rows = page["ResultSet"]["Rows"]
            if page_number == 0:
                data_rows = data_rows[1:]
            for r in data_rows:
                values = [cell.get("VarCharValue") for cell in r["Data"]]
                rows.append({col: _convert(v, t) for col, v, t in zip(columns, values, types)})
        return rows, {"athena_query_execution_id": query_id}

    except QueryError:
        raise
    except (BotoCoreError, ClientError) as exc:
        # The query started but we lost track of it: keep the full reservation and stop
        log["state"] = "UNCERTAIN"
        LEDGER.settle(rid, None, "lost track after start; full reservation kept")
        LEDGER.halt("uncertain athena execution")
        raise QueryError("uncertain", f"Athena status unknown ({type(exc).__name__}); batch halted")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run_query(query_id: str, sql: str, params: list) -> tuple[list[dict], dict]:
    """Run one fixed query with retry-once. Returns (rows, metadata).

    metadata: backend, executed_at, attempts, and athena_query_execution_id on Athena.
    Raises QueryError if both attempts fail.
    """
    runner = _run_athena if config.BACKEND == "athena" else _run_duckdb
    attempts = 0
    while True:
        attempts += 1
        try:
            if config.SIMULATE_FAILURE and config.SIMULATE_FAILURE in query_id:
                raise QueryError("timeout", f"Simulated failure for {query_id}")
            rows, extra = runner(sql, params)
            meta = {"backend": config.BACKEND, "executed_at": now_iso(), "attempts": attempts}
            meta.update(extra)
            return rows, meta
        except QueryError as exc:
            # Never retry when retrying could be unsafe or pointless
            if exc.error_type in NO_RETRY or attempts > config.MAX_RETRIES:
                raise
