"""Reproduce the limited, model-free PR review suite with Python networking blocked.

Run from any directory: python scripts/verify_offline_review.py
This is not the full golden-data acceptance suite or a paid evaluation.
"""

import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    "test_period_binding", "test_wording_boundary", "test_postchecks",
    "test_wbr_contract", "test_wbr_runner", "test_numeric_verifier",
    "test_verified_fact_policy", "test_renderer_boundary", "test_health_diagnostics",
    "test_lakshmi_handoff", "test_lakshmi_verifier_cases",
    "test_analyst_initialization_failure", "test_week_and_wording",
    "test_analyst_adapter", "test_real_olist_acceptance", "test_views", "test_report_renderer",
)
EXCLUDED = frozenset({
    "tests/test_postchecks.py::test_golden_s2_review_state_confidence_uses_rj",
    "tests/test_week_and_wording.py::test_model_cannot_choose_another_week",
    "tests/test_week_and_wording.py::test_wrong_week_repeat_is_still_a_duplicate",
    "tests/test_week_and_wording.py::test_replay_rejects_a_capture_with_evidence_for_another_week",
    "tests/test_views.py::test_every_category_and_state_group_is_shown",
    "tests/test_views.py::test_plan_extremes_match_the_full_result",
    "tests/test_views.py::test_missing_anomaly_statistics_stay_unknown",
    "tests/test_views.py::test_known_anomaly_values_are_not_marked_unavailable",
    "tests/test_views.py::test_partial_result_keeps_guidance_and_missing_values",
    "tests/test_views.py::test_largest_view_stays_small",
})


class ExactDatabaseExclusions:
    def pytest_collection_modifyitems(self, session, config, items):
        found = {item.nodeid for item in items if item.nodeid in EXCLUDED}
        if found != EXCLUDED:
            import pytest
            raise pytest.UsageError("database exclusion manifest changed: " + repr(sorted(EXCLUDED - found)))
        deselected = [item for item in items if item.nodeid in EXCLUDED]
        items[:] = [item for item in items if item.nodeid not in EXCLUDED]
        config.hook.pytest_deselected(items=deselected)



def deny_network(*args, **kwargs):
    raise RuntimeError("Network calls are disabled by the offline review launcher")


def main():
    if len(sys.argv) != 1:
        print("Usage: python scripts/verify_offline_review.py (no arguments)", file=sys.stderr)
        return 2
    # Set safe values before pytest/conftest imports application modules.
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SESSION_TOKEN",
                 "WBR_BATCH_BUDGET_USD", "WBR_MAX_ATHENA_QUERIES", "WBR_SIMULATE_FAILURE"):
        os.environ.pop(name, None)
    os.environ.update(WBR_BACKEND="duckdb", AWS_EC2_METADATA_DISABLED="true",
                      AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing",
                      AWS_DEFAULT_REGION="us-east-2", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            setattr(socket.socket, name, deny_network)
    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        setattr(socket, name, deny_network)
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    print("OFFLINE REVIEW ONLY: Python network calls blocked; no model or AWS evaluation.", flush=True)
    print("10 database-dependent checks excluded. Full golden-data acceptance remains unverified.", flush=True)
    import pytest
    return pytest.main([*(f"tests/{name}.py" for name in TESTS), "-q"],
                       plugins=[ExactDatabaseExclusions()])


if __name__ == "__main__":
    raise SystemExit(main())
