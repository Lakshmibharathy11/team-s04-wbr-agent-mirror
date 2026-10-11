"""Shared settings for the WBR query tools.

Every allowed value, threshold, and metric definition lives here, so the
tools never accept anything that is not listed below.
"""

import os
from datetime import date

# ---------------------------------------------------------------------------
# Backend: where queries run. Switch with the WBR_BACKEND environment variable.
# ---------------------------------------------------------------------------
BACKEND = os.environ.get("WBR_BACKEND", "duckdb")  # "duckdb" or "athena"

# DuckDB (local, no AWS needed)
DUCKDB_PATH = os.environ.get("WBR_DUCKDB_PATH", "data/olist.duckdb")

# Athena (non-secret settings only; credentials come from the AWS CLI profile)
AWS_REGION = os.environ.get("WBR_AWS_REGION", "us-east-2")
ATHENA_WORKGROUP = os.environ.get("WBR_ATHENA_WORKGROUP", "S04-WBR")
ATHENA_DATABASE = os.environ.get("WBR_ATHENA_DATABASE", "s04_wbr")

# Table name prefix. On Athena the golden (planted) tables live in the same
# database as the real ones, named golden_weekly_metrics etc., so evaluation
# sets this to "golden_". On DuckDB golden tables are in a separate file, so "".
TABLE_PREFIX = os.environ.get("WBR_TABLE_PREFIX", "")

# Safety cap on Athena query attempts per process (retries count).
# Empty = no cap. Approved test runs set this so a bug cannot run up queries.
_max_q = os.environ.get("WBR_MAX_ATHENA_QUERIES", "")
MAX_ATHENA_QUERIES = int(_max_q) if _max_q else None

# Query behavior
QUERY_TIMEOUT_SECONDS = 30
MAX_RETRIES = 1  # failure contract: retry once, then report the error

# ---------------------------------------------------------------------------
# Analysis window (weeks start on Monday)
# ---------------------------------------------------------------------------
WINDOW_START = date(2017, 1, 30)
WINDOW_END = date(2018, 8, 13)

# Real incomplete weeks just after the window: allowed, but flagged as not final
INCOMPLETE_WEEKS = {date(2018, 8, 20)}

# ---------------------------------------------------------------------------
# Small-sample thresholds
# ---------------------------------------------------------------------------
NUMERIC_PRECISION_DIGITS = 2  # declared by tool envelopes; historical captures may omit it
MIN_ORDERS = 30   # minimum base for totals and averages (Central Limit Theorem rule of thumb)
MIN_EVENTS = 10   # minimum count of the event itself for rates (e.g. late orders)

# ---------------------------------------------------------------------------
# Groups (decided in stage 1)
# ---------------------------------------------------------------------------
STATE_GROUPS = ["SP", "RJ", "MG", "RS", "PR", "BA", "SC", "Other"]

CATEGORY_GROUPS = [
    "bed_bath_table", "sports_leisure", "health_beauty", "furniture_decor",
    "computers_accessories", "housewares", "watches_gifts", "telephony",
    "cool_stuff", "toys", "garden_tools", "auto", "perfumery", "baby", "Other",
]

DIMENSIONS = {
    "state": {"column": "state_group", "allowed": STATE_GROUPS},
    "category": {"column": "category_group", "allowed": CATEGORY_GROUPS},
}

# ---------------------------------------------------------------------------
# Tables and their column names (the category table names two columns differently)
# ---------------------------------------------------------------------------
TABLES = {
    "weekly_metrics": {
        "revenue": "product_revenue",
        "orders": "orders",
        "grain": "week x state_group",
    },
    "weekly_category_metrics": {
        "revenue": "category_revenue",
        "orders": "orders_with_category",
        "grain": "week x category_group x state_group",
    },
}

# ---------------------------------------------------------------------------
# Metric definitions
#   kind "sum":   value = SUM(numerator)
#   kind "ratio": value = SUM(numerator) / SUM(denominator)
#   base:  the count that decides small-sample warnings
#   event: for rates, the count of the event itself (checked against MIN_EVENTS)
#   scale: 100 for percentages
#   change: "pct" = percent change, "points" = difference (for scores and rates)
# Column names "revenue" and "orders" are placeholders, resolved per table via TABLES.
# ---------------------------------------------------------------------------
METRICS = {
    "product_revenue":    {"kind": "sum",   "numerator": "revenue",           "base": "orders",           "change": "pct"},
    "orders":             {"kind": "sum",   "numerator": "orders",            "base": "orders",           "change": "pct"},
    "items":              {"kind": "sum",   "numerator": "items",             "base": "orders",           "change": "pct"},
    "avg_order_value":    {"kind": "ratio", "numerator": "revenue",           "denominator": "orders",           "base": "orders",           "change": "pct"},
    "items_per_order":    {"kind": "ratio", "numerator": "items",             "denominator": "orders",           "base": "orders",           "change": "pct"},
    "avg_item_price":     {"kind": "ratio", "numerator": "revenue",           "denominator": "items",            "base": "orders",           "change": "pct"},
    "avg_review_score":   {"kind": "ratio", "numerator": "review_sum",        "denominator": "review_count",     "base": "review_count",     "change": "points"},
    "one_star_pct":       {"kind": "ratio", "numerator": "one_star_count",    "denominator": "review_count",     "base": "review_count",     "event": "one_star_count", "scale": 100, "change": "points"},
    "late_delivery_pct":  {"kind": "ratio", "numerator": "late_orders",       "denominator": "delivered_orders", "base": "delivered_orders", "event": "late_orders",    "scale": 100, "change": "points"},
    "avg_delivery_days":  {"kind": "ratio", "numerator": "delivery_days_sum", "denominator": "delivered_orders", "base": "delivered_orders", "change": "pct"},
}

# Cancellation rate only exists on the state table: orders without items cannot
# appear in the category table, so its cancellation counts are incomplete.
STATE_ONLY_METRICS = {
    "cancellation_rate": {"kind": "ratio", "numerator": "canceled_orders", "denominator_sum": ["orders", "canceled_orders"],
                          "base": "orders", "event": "canceled_orders", "scale": 100, "change": "points"},
}

# Metrics get_delivery_signals returns
DELIVERY_SIGNAL_METRICS = ["late_delivery_pct", "avg_delivery_days"]

QUERY_VERSION = "v1"

# ---------------------------------------------------------------------------
# Z-score baseline for flagging unusual changes
# ---------------------------------------------------------------------------
BASELINE_WEEKS = 8      # earlier week-over-week changes used as "normal"
# Z-score thresholds: |z| at or above these is flagged as unusual
Z_THRESHOLD_SINGLE = 2.0      # one metric, one test (get_weekly_metric, get_delivery_signals); ~95% level
Z_THRESHOLD_BREAKDOWN = 3.0   # many groups tested at once (get_breakdown); Bonferroni-adjusted for ~22 groups
# "robust": median and MAD (resistant to a few extreme weeks, e.g. small cells or Black Friday)
# "standard": mean and standard deviation
Z_METHOD = "robust"
MAD_SCALE = 1.4826      # makes MAD comparable to a standard deviation for normal data

# ---------------------------------------------------------------------------
# Failure simulation for tests and the demo (never set in normal runs).
# Example: WBR_SIMULATE_FAILURE=avg_review_score makes any query whose
# query_id contains that text fail with a timeout.
# ---------------------------------------------------------------------------
SIMULATE_FAILURE = os.environ.get("WBR_SIMULATE_FAILURE", "")