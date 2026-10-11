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
    "test_analyst_adapter", "test_real_olist_acceptance", "test_views",
)
EXCLUDED = (
    "golden", "model_cannot_choose", "wrong_week_repeat", "replay_rejects",
    "postcheck_flags_evidence_for_another_week", "every_category", "plan_extremes",
    "missing_anomaly", "known_anomaly", "partial_result_keeps", "largest_view",
)


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
    print("11 database-dependent checks excluded. Full golden-data acceptance remains unverified.", flush=True)
    import pytest
    return pytest.main([*(f"tests/{name}.py" for name in TESTS), "-q", "-k",
                        " and ".join(f"not {name}" for name in EXCLUDED)])


if __name__ == "__main__":
    raise SystemExit(main())
