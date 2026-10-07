"""Builds the standard JSON envelope every tool returns (V1 integration contract).

Fields: tool_name, request_id, status, generated_at, parameters, data,
evidence_refs, warnings, error.
"""

import math
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

VALID_STATUSES = {"ok", "partial", "error"}

# Warning codes the tools can raise, so the verifier can react to them by code
WARNING_CODES = {
    "SMALL_SAMPLE",      # base below MIN_ORDERS, or event count below MIN_EVENTS
    "ZERO_DENOMINATOR",  # a ratio or change could not be calculated
    "INCOMPLETE_WEEK",   # the week is known to have incomplete data
    "NO_DATA",           # a requested group had no rows
    "INSUFFICIENT_HISTORY",  # not enough earlier weeks for a z-score baseline
    "INVALID_DATA",      # rows that fail sanity checks were excluded
}


def new_request_id() -> str:
    """Unique ID for one tool call; claims cite it in supporting_evidence."""
    return "req-" + uuid.uuid4().hex[:12]


def now_iso() -> str:
    """Current time in UTC, ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_number(value):
    """Keep numbers numeric: convert database types to plain int/float, and
    turn missing or invalid values (NULL, NaN, infinity) into None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, int):
        return value
    try:  # numpy and other numeric types
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return int(number) if number.is_integer() and not isinstance(value, float) else number
    except (TypeError, ValueError):
        raise TypeError(f"Expected a number, got {type(value).__name__}: {value!r}")


def make_warning(code: str, message: str, **details) -> dict:
    """One warning, with a fixed code the verifier can check."""
    if code not in WARNING_CODES:
        raise ValueError(f"Unknown warning code: {code}")
    return {"code": code, "message": message, "details": details}


def make_error(error_type: str, message: str) -> dict:
    """Error details for a failed call (validation, timeout, query_failed)."""
    return {"type": error_type, "message": message}


def make_evidence_ref(query_id: str, backend: str, table: str, row_count: int,
                      executed_at: str, athena_query_execution_id: str | None = None) -> dict:
    """Where a result came from, so any number can be traced and re-run."""
    ref = {
        "query_id": query_id,
        "backend": backend,
        "table": table,
        "row_count": row_count,
        "executed_at": executed_at,
    }
    if athena_query_execution_id:
        ref["athena_query_execution_id"] = athena_query_execution_id
    return ref


def _json_safe(value):
    """Dates become ISO strings so the envelope can be written as JSON."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return value


def make_envelope(tool_name: str, status: str, parameters: dict, data=None,
                  evidence_refs=None, warnings=None, error=None,
                  request_id: str | None = None) -> dict:
    """Assemble and check the envelope. Rules enforced:
    - status must be ok, partial, or error
    - an error carries error details and no data
    - ok or partial must carry evidence (missing evidence is never hidden)
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    if status == "error":
        if error is None:
            raise ValueError("An error envelope needs error details")
        data = None
    elif not evidence_refs:
        raise ValueError(f"A '{status}' envelope must include evidence_refs")

    return _json_safe({
        "tool_name": tool_name,
        "request_id": request_id or new_request_id(),
        "status": status,
        "generated_at": now_iso(),
        "parameters": parameters,
        "data": data,
        "evidence_refs": evidence_refs or [],
        "warnings": warnings or [],
        "error": error,
    })