"""Runs the fixed SQL templates on DuckDB (local) or Athena (AWS).

The tools call run_query() and never touch a database directly, so switching
backends is a single setting (WBR_BACKEND) and nothing else changes.
"""

import threading
import time
from pathlib import Path

from tools import config
from tools.envelope import now_iso

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class QueryError(Exception):
    """A query that failed. error_type is 'timeout' or 'query_failed'."""

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
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def _convert(value: str | None, athena_type: str):
    """Athena returns every value as text; convert it back using the column type."""
    if value is None:
        return None
    if athena_type in ("integer", "bigint", "smallint", "tinyint"):
        return int(value)
    if athena_type in ("double", "float", "real", "decimal"):
        return float(value)
    if athena_type == "boolean":
        return value.lower() == "true"
    return value


def _run_athena(sql: str, params: list) -> tuple[list[dict], dict]:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    client = boto3.client("athena", region_name=config.AWS_REGION)
    try:
        request = {
            "QueryString": sql,
            "WorkGroup": config.ATHENA_WORKGROUP,
            "QueryExecutionContext": {"Database": config.ATHENA_DATABASE},
        }
        if params:
            request["ExecutionParameters"] = [_athena_literal(p) for p in params]
        query_id = client.start_query_execution(**request)["QueryExecutionId"]

        # Wait for the query to finish, stopping it if it runs too long
        deadline = time.monotonic() + config.QUERY_TIMEOUT_SECONDS
        while True:
            status = client.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]["Status"]
            state = status["State"]
            if state == "SUCCEEDED":
                break
            if state in ("FAILED", "CANCELLED"):
                raise QueryError("query_failed", status.get("StateChangeReason", state))
            if time.monotonic() > deadline:
                client.stop_query_execution(QueryExecutionId=query_id)
                raise QueryError("timeout", f"Query exceeded {config.QUERY_TIMEOUT_SECONDS} seconds")
            time.sleep(0.5)

        # Read all result pages; the first row of the first page is the header
        rows, columns, types = [], None, None
        for page_number, page in enumerate(
            client.get_paginator("get_query_results").paginate(QueryExecutionId=query_id)
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
        raise QueryError("query_failed", str(exc))


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
        except QueryError:
            if attempts > config.MAX_RETRIES:
                raise