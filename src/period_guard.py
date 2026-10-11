"""Deterministic period checks for live drafts and historical captures."""
from collections.abc import Mapping
from datetime import date, timedelta


def period_problem(envelope, week):
    params = envelope.get("parameters")
    if not isinstance(params, Mapping) or params.get("week") != week:
        return "evidence_week_mismatch"
    expected = (date.fromisoformat(week) - timedelta(days=7)).isoformat()
    # A failed validation call may not have reached comparison calculation.
    if envelope.get("status") != "error" or "compare_week" in params:
        if params.get("compare_week") != expected:
            return "evidence_compare_week_mismatch"
    return None
