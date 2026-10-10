"""Tool status rules on the golden data.

A missing state group in an EARLIER baseline week must not make a result partial:
the reported values are complete; that week is only left out of the baseline.
A missing group in a COMPARED week still makes it partial (S7).

    python -m pytest tests/test_tool_status.py -q
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.wbr_tools import get_weekly_metric  # noqa: E402

KEYS = {s["id"].split("_")[0]: s for s in
        json.loads((ROOT / "tests" / "golden" / "answer_keys.json").read_text())["scenarios"]}
S7 = date.fromisoformat(KEYS["S7"]["week"])


def codes(env):
    return {w["code"] for w in env["warnings"]}


def test_compared_week_missing_is_partial():
    for week in (S7, S7 + timedelta(days=7)):      # S7 as current week, then as previous week
        env = get_weekly_metric("product_revenue", week.isoformat())
        assert env["status"] == "partial" and "NO_DATA" in codes(env), week


def test_baseline_week_missing_is_not_partial():
    week = S7 + timedelta(days=21)                  # S7 is only in the baseline here
    env = get_weekly_metric("product_revenue", week.isoformat())
    assert "NO_DATA" not in codes(env)
    assert S7.isoformat() in env["data"]["baseline"].get("excluded_weeks", [])
