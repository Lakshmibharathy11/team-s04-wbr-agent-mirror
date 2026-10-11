"""Build the golden dataset: copies of the weekly tables with planted, labeled
test scenarios, plus an answer key, then check the tools detect each scenario.

    python scripts/build_golden_dataset.py

- Source data/olist.duckdb is opened read-only and never changed.
- Planted copies go to data/golden.duckdb (ignored by Git, like all .duckdb files).
- Answer keys go to tests/golden/answer_keys.json, OUTSIDE the database, so the
  agent can never read the expected answers (no data leakage).
- All planted numbers are SYNTHETIC test scenarios applied to real Olist weeks.
"""

import json
import sys
from datetime import date
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import config  # noqa: E402

SOURCE_DB = ROOT / "data" / "olist.duckdb"
GOLDEN_DB = ROOT / "data" / "golden.duckdb"
ANSWER_KEYS = ROOT / "tests" / "golden" / "answer_keys.json"

# Additive columns in each table (category names map to the state-table names)
STATE_COLS = ["orders", "product_revenue", "items", "freight", "canceled_orders", "review_count",
              "review_sum", "one_star_count", "delivered_orders", "late_orders", "delivery_days_sum"]
CATEGORY_COLS = ["orders_with_category", "category_revenue", "items", "freight", "canceled_orders",
                 "review_count", "review_sum", "one_star_count", "delivered_orders", "late_orders",
                 "delivery_days_sum"]
CAT_TO_STATE = dict(zip(CATEGORY_COLS, STATE_COLS))
FLOAT_COLS = {"product_revenue", "category_revenue", "freight", "review_sum"}

# ---------------------------------------------------------------------------
# Scenarios. Weeks are 11 weeks apart so one planted week never sits inside
# another scenario's 8-week z-score baseline.
# ---------------------------------------------------------------------------
SCENARIOS = [
    {
        "id": "S1_category_drop",
        "kind": "category_drop",
        "week": "auto",   # "auto" = chosen by scanning the original data for a calm week; or set a date
        "state": "SP",
        "category": "health_beauty",
        "factor": 0.15,  # this cell keeps 15% of its volume: an 85% drop (must clear the breakdown threshold of 3)
        "description": "health_beauty sales in SP fall 85% (orders, revenue, items, reviews, deliveries scaled).",
    },
    {
        "id": "S2_delivery_and_reviews",
        "kind": "delivery_reviews",
        "week": "auto",
        "state": "RJ",
        "late_rate": 0.30,        # late deliveries set to 30% of delivered orders
        "review_drop": 0.8,       # average review score lowered by 0.8 points
        "one_star_add": 0.15,     # extra 1-star reviews: +15% of reviewed orders
        "description": "In RJ, late deliveries rise to 30% and the average review score falls 0.8 points; revenue unchanged.",
    },
    {
        "id": "S3_unexplained_drop",
        "kind": "broad_drop",
        "week": "auto",
        "state": "MG",
        "factor": 0.5,   # every MG category keeps 50%: a broad 50% drop
        "description": "MG volume falls 50% evenly across all categories, with no change in delivery rates.",
    },
    {
        "id": "S4_incomplete_week",
        "kind": "none",
        "week": "2018-08-20",
        "description": "Real data: the week of 2018-08-20 is incomplete (end of data collection). Nothing planted.",
    },
    {
        "id": "S6_garbage_data",
        "kind": "garbage",
        "week": "2018-01-15",
        "state": "PR",
        "description": "Corrupted row: PR reports more late orders than delivered orders (impossible).",
    },
    {
        "id": "S7_missing_rows",
        "kind": "missing_rows",
        "week": "2018-04-23",
        "state": "BA",
        "description": "BA's row is missing from weekly_metrics for this week (data did not arrive).",
    },
    {
        "id": "S8_zero_denominator",
        "kind": "zero_denominator",
        "week": "2018-02-19",
        "state": "SC",
        "description": "SC reports zero delivered orders this week, so late delivery % cannot be calculated.",
    },
    {
        "id": "S5_tool_failure",
        "kind": "simulated_failure",
        "week": "2018-07-02",
        "simulate": "avg_review_score",
        "description": "The review-score query fails (WBR_SIMULATE_FAILURE=avg_review_score); other metrics succeed.",
    },
]


# ---------------------------------------------------------------------------
# Calm-week selection: evaluate the ORIGINAL data before planting, so a planted
# scenario never competes with a real movement in the same slice.
# ---------------------------------------------------------------------------
from datetime import timedelta  # noqa: E402

ONE_WEEK = timedelta(days=7)
BREAKDOWN_CALM = 2.5   # real |z| must stay below this in breakdowns (margin under the threshold of 3)
SINGLE_CALM = 1.5      # real |z| must stay below this for single metrics (margin under 2)
MIN_GAP_WEEKS = 11     # S1-S3 at least this far apart (keeps planted weeks out of each other's baselines)
MIN_CELL_ORDERS = 30   # S1 cell needs enough real orders for a planted drop to stand out
SKIP_WEEKS = {date(2017, 11, 20), date(2017, 11, 27)}   # Black Friday and the week after


def _candidate_weeks():
    """Weeks with a full z-score baseline, latest first (higher volume)."""
    first = config.WINDOW_START + ONE_WEEK * (config.BASELINE_WEEKS + 2)
    weeks, w = [], config.WINDOW_END
    while w >= first:
        weeks.append(w)
        w -= ONE_WEEK
    return weeks


def _fixed_weeks():
    """Weeks used by the fixed scenarios (S4-S8), plus the week after each."""
    blocked = set()
    for s in SCENARIOS:
        if s["kind"] in ("garbage", "missing_rows", "zero_denominator", "none", "simulated_failure"):
            w = date.fromisoformat(s["week"])
            blocked |= {w, w + ONE_WEEK}
    return blocked


def _max_abs_z(groups):
    zs = [abs(g["z_score"]) for g in groups if g.get("z_score") is not None]
    return max(zs) if zs else 0.0


def _unusual_names(groups, limit):
    return [f"{g['group']} ({g['change']}%, z {g['z_score']})" for g in groups
            if g.get("z_score") is not None and abs(g["z_score"]) >= limit]


def _calm_for(s, week, tools, con, anomalies):
    """True if the slices this scenario will be checked on are quiet in the original data."""
    get_breakdown, get_weekly_metric, get_delivery_signals = tools
    w = week.isoformat()
    if s["kind"] == "category_drop":
        cats = get_breakdown("product_revenue", w, "category")["data"]["groups"]
        states = get_breakdown("product_revenue", w, "state", category=s["category"])["data"]["groups"]
        anomalies.extend((w, "category", x) for x in _unusual_names(cats, config.Z_THRESHOLD_BREAKDOWN))
        prev = con.execute(
            "SELECT orders_with_category FROM weekly_category_metrics "
            "WHERE week_start = ? AND state_group = ? AND category_group = ?",
            [week - ONE_WEEK, s["state"], s["category"]]).fetchone()
        enough = prev is not None and prev[0] >= MIN_CELL_ORDERS
        return enough and _max_abs_z(cats) < BREAKDOWN_CALM and _max_abs_z(states) < BREAKDOWN_CALM
    if s["kind"] == "delivery_reviews":
        rev = get_weekly_metric("avg_review_score", w, state=s["state"])["data"]
        late = get_delivery_signals(w, state=s["state"])["data"]["metrics"]["late_delivery_pct"]
        return all(x["z_score"] is not None and abs(x["z_score"]) < SINGLE_CALM for x in (rev, late))
    if s["kind"] == "broad_drop":
        states = get_breakdown("product_revenue", w, "state")["data"]["groups"]
        cats = get_breakdown("product_revenue", w, "category", state=s["state"])["data"]["groups"]
        late = get_delivery_signals(w, state=s["state"])["data"]["metrics"]["late_delivery_pct"]
        anomalies.extend((w, "state", x) for x in _unusual_names(states, config.Z_THRESHOLD_BREAKDOWN))
        worsening = late["z_score"] is not None and late["z_score"] >= SINGLE_CALM
        return _max_abs_z(states) < BREAKDOWN_CALM and _max_abs_z(cats) < BREAKDOWN_CALM and not worsening
    return True


def choose_weeks():
    """Fill in every scenario week set to "auto", using the original data (read-only)."""
    config.DUCKDB_PATH = str(SOURCE_DB)
    from tools.wbr_tools import get_breakdown, get_delivery_signals, get_weekly_metric
    tools = (get_breakdown, get_weekly_metric, get_delivery_signals)
    con = duckdb.connect(str(SOURCE_DB), read_only=True)
    blocked = _fixed_weeks()
    chosen = [date.fromisoformat(s["week"]) for s in SCENARIOS
              if s["week"] != "auto" and s["kind"] in PLANTERS and s["kind"] not in
              ("garbage", "missing_rows", "zero_denominator")]
    anomalies = []
    try:
        for s in SCENARIOS:
            if s["week"] != "auto":
                continue
            for week in _candidate_weeks():
                if week in blocked or week in SKIP_WEEKS or (week - ONE_WEEK) in SKIP_WEEKS:
                    continue
                if any(abs((week - c).days) < MIN_GAP_WEEKS * 7 for c in chosen):
                    continue
                if _calm_for(s, week, tools, con, anomalies):
                    s["week"] = week.isoformat()
                    chosen.append(week)
                    print(f"{s['id']}: calm week {s['week']}")
                    break
            else:
                raise RuntimeError(f"{s['id']}: no calm week found; relax the calm limits or set a week")
    finally:
        con.close()
    return sorted(set(anomalies))


# ---------------------------------------------------------------------------
# Planting
# ---------------------------------------------------------------------------
def _scaled(value, factor, col):
    new = value * factor
    return new if col in FLOAT_COLS else int(round(new))


def plant_category_drop(con, s):
    """Scale one state x category cell, and subtract the same amounts from that
    state's row in weekly_metrics so the two tables stay consistent."""
    week = date.fromisoformat(s["week"])
    row = con.execute(
        f"SELECT {', '.join(CATEGORY_COLS)} FROM weekly_category_metrics "
        "WHERE week_start = ? AND state_group = ? AND category_group = ?",
        [week, s["state"], s["category"]]).fetchone()
    if row is None:
        raise RuntimeError(f"{s['id']}: no row for {s['state']} x {s['category']} in {week}")
    old = dict(zip(CATEGORY_COLS, row))
    new = {c: _scaled(old[c], s["factor"], c) for c in CATEGORY_COLS}
    con.execute(
        f"UPDATE weekly_category_metrics SET {', '.join(f'{c} = ?' for c in CATEGORY_COLS)} "
        "WHERE week_start = ? AND state_group = ? AND category_group = ?",
        [new[c] for c in CATEGORY_COLS] + [week, s["state"], s["category"]])
    deltas = {CAT_TO_STATE[c]: old[c] - new[c] for c in CATEGORY_COLS}
    con.execute(
        f"UPDATE weekly_metrics SET {', '.join(f'{c} = {c} - ?' for c in STATE_COLS)} "
        "WHERE week_start = ? AND state_group = ?",
        [deltas[c] for c in STATE_COLS] + [week, s["state"]])


def plant_delivery_reviews(con, s):
    """Raise late deliveries and lower review scores for one state, in both tables."""
    week = date.fromisoformat(s["week"])
    for table, key_cols in (("weekly_metrics", ["state_group"]),
                            ("weekly_category_metrics", ["state_group", "category_group"])):
        rows = con.execute(
            f"SELECT {', '.join(key_cols)}, delivered_orders, review_count, review_sum, one_star_count "
            f"FROM {table} WHERE week_start = ? AND state_group = ?", [week, s["state"]]).fetchall()
        for r in rows:
            keys, (delivered, reviews, review_sum, one_star) = r[:len(key_cols)], r[len(key_cols):]
            late = int(round(delivered * s["late_rate"]))
            new_sum = max(float(reviews), review_sum - s["review_drop"] * reviews)  # scores stay >= 1
            new_one_star = min(reviews, one_star + int(round(s["one_star_add"] * reviews)))
            where = " AND ".join(f"{k} = ?" for k in key_cols)
            con.execute(
                f"UPDATE {table} SET late_orders = ?, review_sum = ?, one_star_count = ? "
                f"WHERE week_start = ? AND {where}", [late, new_sum, new_one_star, week, *keys])


def plant_broad_drop(con, s):
    """Scale every row of one state by the same factor, in both tables."""
    week = date.fromisoformat(s["week"])
    for table, cols in (("weekly_metrics", STATE_COLS), ("weekly_category_metrics", CATEGORY_COLS)):
        sets = ", ".join(
            f"{c} = {c} * {s['factor']}" if c in FLOAT_COLS
            else f"{c} = CAST(ROUND({c} * {s['factor']}) AS BIGINT)" for c in cols)
        con.execute(f"UPDATE {table} SET {sets} WHERE week_start = ? AND state_group = ?",
                    [week, s["state"]])


def plant_garbage(con, s):
    """An impossible row: more late orders than delivered orders."""
    con.execute("UPDATE weekly_metrics SET late_orders = delivered_orders + 50 "
                "WHERE week_start = ? AND state_group = ?", [date.fromisoformat(s["week"]), s["state"]])


def plant_missing_rows(con, s):
    """Remove one state's row for one week, as if its data never arrived."""
    con.execute("DELETE FROM weekly_metrics WHERE week_start = ? AND state_group = ?",
                [date.fromisoformat(s["week"]), s["state"]])


def plant_zero_denominator(con, s):
    """Zero delivered orders (still a valid row), so delivery rates have no denominator."""
    con.execute("UPDATE weekly_metrics SET delivered_orders = 0, late_orders = 0, delivery_days_sum = 0 "
                "WHERE week_start = ? AND state_group = ?", [date.fromisoformat(s["week"]), s["state"]])


PLANTERS = {"category_drop": plant_category_drop, "delivery_reviews": plant_delivery_reviews,
            "broad_drop": plant_broad_drop, "garbage": plant_garbage,
            "missing_rows": plant_missing_rows, "zero_denominator": plant_zero_denominator}


def build():
    if GOLDEN_DB.exists():
        GOLDEN_DB.unlink()
    con = duckdb.connect(str(GOLDEN_DB))
    con.execute(f"ATTACH '{SOURCE_DB.as_posix()}' AS src (READ_ONLY)")
    for table in ("weekly_metrics", "weekly_category_metrics"):
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM src.{table}")
    con.execute("DETACH src")
    for s in SCENARIOS:
        if s["kind"] in PLANTERS:
            PLANTERS[s["kind"]](con, s)
    con.close()


# ---------------------------------------------------------------------------
# Answer keys: what a correct agent should report for each scenario
# ---------------------------------------------------------------------------
def answer_keys():
    return {
        "dataset": "SYNTHETIC test scenarios planted on real Olist weekly data",
        "method": "Detection uses single-metric z-scores (threshold 2); attribution uses breakdown rank and "
                  "share of change. The breakdown threshold (3) flags standalone anomalies when scanning.",
        "built_from": "data/olist.duckdb (unchanged)",
        "golden_db": "data/golden.duckdb",
        "scenarios": [
            {
                "id": "S1_category_drop", "week": SCENARIOS[0]["week"], "question_id": "Q3",
                "planted": SCENARIOS[0]["description"],
                "expected": {
                    "top_category_by_revenue_change": "health_beauty",
                    "category_drop_unusual_single_metric": True,
                    "top_state_within_category": "SP",
                    "largest_share_of_category_drop": "SP",
                    "claim_type": "fact",
                    "report_status": "COMPLETE",
                },
                "agent_should_say": "health_beauty revenue fell sharply, concentrated in SP.",
            },
            {
                "id": "S2_delivery_and_reviews", "week": SCENARIOS[1]["week"], "question_id": "Q2",
                "planted": SCENARIOS[1]["description"],
                "expected": {
                    "state_with_review_drop": "RJ",
                    "review_drop_is_unusual": True,
                    "late_delivery_rise_is_unusual": True,
                    "claim_type": "observed_pattern",
                    "causal_language_allowed": False,
                    "report_status": "COMPLETE",
                },
                "agent_should_say": "Average review score fell in RJ while late deliveries rose there; "
                                    "this is an observed pattern, not an established cause.",
            },
            {
                "id": "S3_unexplained_drop", "week": SCENARIOS[2]["week"], "question_id": "Q1",
                "planted": SCENARIOS[2]["description"],
                "expected": {
                    "top_state_by_revenue_change": "MG",
                    "largest_share_of_total_change": "MG",
                    "state_drop_unusual_single_metric": True,
                    "single_category_driver": False,
                    "delivery_worsening_unusual": False,
                    "claim_type": "unexplained",
                    "requires_human_review": True,
                    "report_status": "COMPLETE",
                },
                "agent_should_say": "MG revenue fell sharply across all categories with no delivery change; "
                                    "unexplained, needs analyst review.",
            },
            {
                "id": "S4_incomplete_week", "week": "2018-08-20", "question_id": "all",
                "planted": SCENARIOS[3]["description"],
                "expected": {"tool_status": "partial", "warning": "INCOMPLETE_WEEK", "report_status": "PARTIAL"},
                "agent_should_say": "Numbers for this week are not final; no conclusions drawn.",
            },
            {
                "id": "S5_tool_failure", "week": "2018-07-02", "question_id": "Q2",
                "planted": next(x for x in SCENARIOS if x["id"] == "S5_tool_failure")["description"],
                "run_with": {"WBR_SIMULATE_FAILURE": "avg_review_score"},
                "expected": {
                    "review_score_status": "error",
                    "revenue_status": "ok",
                    "orders_status": "ok",
                    "report_status": "PARTIAL",
                    "values_invented": False,
                },
                "agent_should_say": "Review score unavailable this week; revenue and orders reported.",
            },
            {
                "id": "S6_garbage_data", "week": "2018-01-15", "question_id": "Q2",
                "planted": "Corrupted row: PR reports more late orders than delivered orders (impossible).",
                "expected": {
                    "delivery_signals_PR_status": "error",
                    "error_type": "malformed_data",
                    "breakdown_status": "partial",
                    "breakdown_PR_values": None,
                    "warning": "INVALID_DATA",
                    "report_status": "PARTIAL",
                    "values_invented": False,
                },
                "agent_should_say": "PR delivery data failed validation this week and is excluded.",
            },
            {
                "id": "S7_missing_rows", "week": "2018-04-23", "question_id": "Q1",
                "planted": "BA's row is missing from weekly_metrics for this week (data did not arrive).",
                "expected": {
                    "BA_revenue_status": "partial",
                    "BA_revenue_current": None,
                    "total_revenue_status": "partial",
                    "warning": "NO_DATA",
                    "missing_treated_as_zero": False,
                    "report_status": "PARTIAL",
                },
                "agent_should_say": "BA data is missing this week; totals are not reported as a drop.",
            },
            {
                "id": "S8_zero_denominator", "week": "2018-02-19", "question_id": "Q2",
                "planted": "SC reports zero delivered orders this week, so late delivery % cannot be calculated.",
                "expected": {
                    "SC_late_delivery_pct_current": None,
                    "warning": "ZERO_DENOMINATOR",
                    "fabricated_percentage": False,
                },
                "agent_should_say": "SC late delivery rate cannot be calculated this week (no deliveries).",
            },
            {
                "id": "S9_invalid_agent_input", "week": "n/a", "question_id": "all",
                "planted": "Nothing planted: the agent requests an unknown metric, a non-Monday, or a week outside the window.",
                "expected": {"tool_status": "error", "error_type": "validation",
                             "agent_behavior": "corrects the input and retries, or reports the limitation"},
                "agent_should_say": "Nothing about the invalid request; it retries with valid inputs.",
            },
            {
                "id": "S10_insufficient_history", "week": "2017-02-13", "question_id": "Q1",
                "planted": "Nothing planted: an early week has fewer than 8 earlier weeks for a baseline.",
                "expected": {"z_score": None, "warning": "INSUFFICIENT_HISTORY",
                             "agent_behavior": "reports the change without calling it unusual"},
                "agent_should_say": "Change reported; too little history to judge whether it is unusual.",
            },
        ],
        "verifier_test_cases": [
            {
                "id": "V1_unsupported_cause", "scenario": "S2_delivery_and_reviews",
                "claim": "Average review score fell in RJ because late deliveries rose.",
                "claim_type": "observed_pattern", "causal_language_allowed": False,
                "expected_verifier_outcome": "downgrade or flag",
                "reason": "Causal wording without causal evidence; the data shows association only.",
            },
            {
                "id": "V2_number_not_in_evidence", "scenario": "S1_category_drop",
                "claim": "health_beauty revenue fell 95% in SP.",
                "claim_type": "fact",
                "expected_verifier_outcome": "remove",
                "reason": "The cited tool result reports about an 85% drop; 95% appears in no evidence.",
            },
            {
                "id": "V3_claim_on_unavailable_evidence", "scenario": "S5_tool_failure",
                "claim": "Average review score was stable this week.",
                "claim_type": "fact",
                "expected_verifier_outcome": "remove",
                "reason": "The review-score tool call failed; no evidence supports any review claim.",
            },
            {
                "id": "V4_unrelated_metrics_linked", "scenario": "any",
                "claim": "Revenue fell because reviews fell.",
                "claim_type": "observed_pattern", "causal_language_allowed": False,
                "expected_verifier_outcome": "downgrade or flag",
                "reason": "From the demo script: both declined, but the report does not establish cause.",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Validation: do the tools detect each planted scenario?
# ---------------------------------------------------------------------------
def validate():
    config.DUCKDB_PATH = str(GOLDEN_DB)
    from tools.wbr_tools import get_breakdown, get_delivery_signals, get_weekly_metric

    results = []

    def check(scenario, name, passed, detail):
        results.append((scenario, name, "PASS" if passed else "FAIL", detail))

    # S1: detect with a single-metric test, attribute with breakdown rank and share
    w = SCENARIOS[0]["week"]
    sc = SCENARIOS[0]
    cats = get_breakdown("product_revenue", w, "category")["data"]["groups"]
    top = cats[0]
    check("S1", f"{sc['category']} ranks first among categories",
          top["group"] == sc["category"], f"{top['group']} change {top['change']}% z {top['z_score']}")
    single = get_weekly_metric("product_revenue", w, category=sc["category"])["data"]
    check("S1", f"{sc['category']} revenue drop is unusual (single metric)",
          single["is_unusual"] is True and single["change"] < 0,
          f"change {single['change']}% z {single['z_score']} (threshold {single['z_threshold']})")
    states = get_breakdown("product_revenue", w, "state", category=sc["category"])["data"]["groups"]
    top = states[0]
    biggest_share = max((g for g in states if g.get("share_of_total_change_pct") is not None),
                        key=lambda g: g["share_of_total_change_pct"])
    check("S1", f"{sc['state']} ranks first within {sc['category']} with the largest share of its drop",
          top["group"] == sc["state"] and biggest_share["group"] == sc["state"],
          f"{top['group']} change {top['change']}%, share {biggest_share['share_of_total_change_pct']}%")

    # S2
    w = SCENARIOS[1]["week"]
    rev = get_weekly_metric("avg_review_score", w, state="RJ")["data"]
    check("S2", "RJ review score drop is unusual",
          rev["change"] is not None and rev["change"] < 0 and rev["is_unusual"],
          f"change {rev['change']} points z {rev['z_score']}")
    late = get_delivery_signals(w, state="RJ")["data"]["metrics"]["late_delivery_pct"]
    check("S2", "RJ late delivery rise is unusual",
          late["change"] is not None and late["change"] > 0 and late["is_unusual"],
          f"change {late['change']} points z {late['z_score']}")

    # S3: detect with a single-metric test, attribute with breakdown rank and share
    w = SCENARIOS[2]["week"]
    sc3 = SCENARIOS[2]
    states = get_breakdown("product_revenue", w, "state")["data"]["groups"]
    top = states[0]
    biggest_share = max((g for g in states if g.get("share_of_total_change_pct") is not None),
                        key=lambda g: g["share_of_total_change_pct"])
    check("S3", f"{sc3['state']} ranks first among states with the largest share of the change",
          top["group"] == sc3["state"] and biggest_share["group"] == sc3["state"],
          f"{top['group']} change {top['change']}%, share {biggest_share['share_of_total_change_pct']}%")
    single = get_weekly_metric("product_revenue", w, state=sc3["state"])["data"]
    check("S3", f"{sc3['state']} revenue drop is unusual (single metric)",
          single["is_unusual"] is True and single["change"] < 0,
          f"change {single['change']}% z {single['z_score']} (threshold {single['z_threshold']})")
    mg_cats = get_breakdown("product_revenue", w, "category", state="MG")["data"]["groups"]
    falling = [g for g in mg_cats if g["change"] is not None and g["change"] < -20]
    check("S3", "drop is broad (several MG categories fall)", len(falling) >= 3,
          f"{len(falling)} categories fell more than 20%")
    late = get_delivery_signals(w, state="MG")["data"]["metrics"]["late_delivery_pct"]
    worsening = late["is_unusual"] is True and (late["change"] or 0) > 0
    check("S3", "no unusual delivery worsening in MG", not worsening,
          f"late change {late['change']} points z {late['z_score']}")

    # S4
    env = get_weekly_metric("orders", "2018-08-20")
    check("S4", "incomplete week returns partial with INCOMPLETE_WEEK",
          env["status"] == "partial" and any(x["code"] == "INCOMPLETE_WEEK" for x in env["warnings"]),
          f"status {env['status']}")

    # S5
    config.SIMULATE_FAILURE = "avg_review_score"
    try:
        w = "2018-07-02"
        review = get_weekly_metric("avg_review_score", w)
        revenue = get_weekly_metric("product_revenue", w)
        orders = get_weekly_metric("orders", w)
    finally:
        config.SIMULATE_FAILURE = ""
    check("S5", "review fails, revenue and orders succeed, nothing invented",
          review["status"] == "error" and review["data"] is None
          and revenue["status"] == "ok" and orders["status"] == "ok",
          f"review {review['status']}, revenue {revenue['status']}, orders {orders['status']}")

    # S6
    w = "2018-01-15"
    env = get_delivery_signals(w, state="PR")
    check("S6", "garbage row: PR delivery signals rejected as malformed",
          env["status"] == "error" and env["error"]["type"] == "malformed_data" and env["data"] is None,
          f"status {env['status']}, error {env['error'] and env['error']['type']}")
    br = get_breakdown("late_delivery_pct", w, "state")
    pr = next(g for g in br["data"]["groups"] if g["group"] == "PR")
    others_ok = all(g["current"] is not None for g in br["data"]["groups"] if g["group"] != "PR")
    check("S6", "breakdown: PR excluded, other states still reported",
          br["status"] == "partial" and pr["current"] is None and others_ok
          and any(x["code"] == "INVALID_DATA" for x in br["warnings"]),
          f"status {br['status']}, PR current {pr['current']}")

    # S7
    w = "2018-04-23"
    ba = get_weekly_metric("product_revenue", w, state="BA")
    check("S7", "missing BA row: partial, value empty (not zero)",
          ba["status"] == "partial" and ba["data"]["current"] is None,
          f"status {ba['status']}, current {ba['data']['current']}")
    total = get_weekly_metric("product_revenue", w)
    check("S7", "total revenue not reported as a drop when a state is missing",
          total["status"] == "partial" and total["data"]["current"] is None
          and any(x["code"] == "NO_DATA" for x in total["warnings"]),
          f"status {total['status']}, current {total['data']['current']}")

    # S8
    w = "2018-02-19"
    late = get_delivery_signals(w, state="SC")
    pct = late["data"]["metrics"]["late_delivery_pct"]
    check("S8", "zero delivered orders: late % empty with ZERO_DENOMINATOR",
          pct["current"] is None and any(x["code"] == "ZERO_DENOMINATOR" for x in late["warnings"])
          and late["status"] == "partial",
          f"status {late['status']}, late % {pct['current']}")

    # S9
    bad = [get_weekly_metric("profit", "2018-06-04"), get_weekly_metric("orders", "2018-06-05"),
           get_weekly_metric("orders", "2016-10-03"), get_breakdown("orders", "2018-06-04", "city")]
    check("S9", "invalid agent inputs return validation errors",
          all(b["status"] == "error" and b["error"]["type"] == "validation" for b in bad),
          f"{sum(b['status'] == 'error' for b in bad)}/4 rejected")

    # S10
    early = get_weekly_metric("product_revenue", "2017-02-13")
    check("S10", "early week: no z-score, INSUFFICIENT_HISTORY",
          early["data"]["z_score"] is None and any(x["code"] == "INSUFFICIENT_HISTORY" for x in early["warnings"]),
          f"z {early['data']['z_score']}")

    return results


if __name__ == "__main__":
    print("Scanning the original data for calm weeks...")
    anomalies = choose_weeks()
    build()
    if anomalies:
        print("\nReal anomalies found in the original data while scanning (not planted):")
        for week, kind, text in anomalies:
            print(f"  {week}  {kind:8s} {text}")
    ANSWER_KEYS.parent.mkdir(parents=True, exist_ok=True)
    ANSWER_KEYS.write_text(json.dumps(answer_keys(), indent=2))
    print(f"Golden database: {GOLDEN_DB}")
    print(f"Answer keys:     {ANSWER_KEYS}\n")
    results = validate()
    for scenario, name, status, detail in results:
        print(f"{status}  {scenario}  {name:55s} {detail}")
    failed = sum(r[2] == "FAIL" for r in results)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)
