"""Regression tests for what the model sees (agent/views.py) and the prompt's
reading rules. Real tools on the golden DuckDB tables; no model, no AWS.

    python -m pytest tests/test_views.py -q
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.prompts import SYSTEM_PROMPT                     # noqa: E402
from agent.views import concise_view                       # noqa: E402
from tools import config                                   # noqa: E402
from tools.wbr_tools import get_breakdown, get_weekly_metric  # noqa: E402

KEYS = {s["id"].split("_")[0]: s for s in
        json.loads((ROOT / "tests" / "golden" / "answer_keys.json").read_text())["scenarios"]}


def test_every_category_and_state_group_is_shown():
    week = KEYS["S1"]["week"]
    cats = concise_view(get_breakdown("product_revenue", week, "category"))
    states = concise_view(get_breakdown("product_revenue", week, "state"))
    assert {g["group"] for g in cats["groups"]} == set(config.CATEGORY_GROUPS)
    assert {g["group"] for g in states["groups"]} == set(config.STATE_GROUPS)
    assert "groups_not_shown" not in cats


def test_plan_extremes_match_the_full_result():
    # Each group the plan asks for (most negative absolute_change, largest share)
    # must be visible, whatever its z-score rank
    week = KEYS["S1"]["week"]
    env = get_breakdown("product_revenue", week, "category")
    view = concise_view(env)

    def pick(groups, key, fn):
        vals = [g for g in groups if g.get(key) is not None]
        return fn(vals, key=lambda g: g[key])["group"]
    for key, fn in (("absolute_change", min), ("share_of_total_change_pct", max)):
        assert pick(view["groups"], key, fn) == pick(env["data"]["groups"], key, fn)


def test_extreme_with_lowest_z_is_not_hidden():
    groups = [{"group": f"g{i}", "change": -1.0, "change_type": "pct", "z_score": 5.0 - i * 0.3,
               "is_unusual": False, "absolute_change": -10.0, "share_of_total_change_pct": 1.0}
              for i in range(15)]
    groups[-1].update(absolute_change=-9999.0, share_of_total_change_pct=90.0, z_score=0.01)
    env = {"request_id": "r", "tool_name": "get_breakdown", "status": "ok", "error": None,
           "warnings": [], "parameters": {"week": "2018-01-01"},
           "data": {"metric": "product_revenue", "dimension": "category", "groups": groups}}
    shown = {g["group"] for g in concise_view(env)["groups"]}
    assert "g14" in shown and len(shown) == 15


def test_missing_anomaly_statistics_stay_unknown():
    # S10: too little history for a baseline, so no z-score
    view = concise_view(get_weekly_metric("product_revenue", KEYS["S10"]["week"]))
    assert "is_unusual" in view and view["is_unusual"] is None
    assert view["anomaly_check"] == "unavailable"
    assert view["z_score"] is None
    assert "INSUFFICIENT_HISTORY" in view.get("warnings", [])


def test_known_anomaly_values_are_not_marked_unavailable():
    view = concise_view(get_weekly_metric("product_revenue", KEYS["S1"]["week"]))
    assert view["is_unusual"] in (True, False)
    assert "anomaly_check" not in view


def test_partial_result_keeps_guidance_and_missing_values():
    # S7: one state's row is missing, so the total is partial
    config.DUCKDB_PATH = str(ROOT / "data" / "golden.duckdb")
    view = concise_view(get_weekly_metric("product_revenue", KEYS["S7"]["week"]))
    assert view["status"] == "partial" and "guidance" in view
    assert "NO_DATA" in view.get("warnings", [])
    states = concise_view(get_breakdown("product_revenue", KEYS["S7"]["week"], "state"))
    missing = [g for g in states["groups"] if g.get("current", 0) is None]
    assert missing, "the missing state's null value must be visible, not dropped"


def test_error_result_shows_no_values():
    config.SIMULATE_FAILURE = "avg_review_score"
    try:
        view = concise_view(get_weekly_metric("avg_review_score", KEYS["S5"]["week"]))
    finally:
        config.SIMULATE_FAILURE = ""
    assert view["status"] == "error" and "guidance" in view and "error" in view
    assert "current" not in view and "is_unusual" not in view


def test_prompt_has_three_anomaly_branches_and_no_fixture_answers():
    assert "is_unusual true" in SYSTEM_PROMPT
    assert "is_unusual false" in SYSTEM_PROMPT
    assert "is_unusual null" in SYSTEM_PROMPT and "unknown" in SYSTEM_PROMPT
    for answer in ("SP", "health_beauty", "RJ", "MG"):
        assert answer not in SYSTEM_PROMPT.replace("STATE", "")


def test_largest_view_stays_small():
    # More groups means more context; keep it bounded (the token guards still apply)
    view = concise_view(get_breakdown("product_revenue", KEYS["S1"]["week"], "category"))
    assert len(json.dumps(view)) < 8000


def test_prompt_rules_that_graders_depend_on():
    # S5: a failed call must never be cited, and a claim needs valid evidence
    assert "never cite it" in SYSTEM_PROMPT
    assert "Every claim cites at least one result whose status is ok or partial" in SYSTEM_PROMPT
    # S3: no invented delivery pattern from a normal-range delivery change
    assert "counts as a change only when its is_unusual is true" in SYSTEM_PROMPT
    # S2: each answer names the group it located
    assert "Q2 names REVIEW STATE" in SYSTEM_PROMPT
    # banned causal words match the post-check exactly
    from agent.postchecks import CAUSAL_PATTERN
    words = CAUSAL_PATTERN.pattern.split("(")[1].split(")")[0].split("|")
    for w in words:
        assert w in SYSTEM_PROMPT, w
