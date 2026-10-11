"""The fixed SQL templates. The AI never writes SQL: it only picks a tool and
fills in parameters, which are checked against config before reaching here.

Column and table names come only from config (they cannot be passed as query
parameters); filter values are always passed as parameters (the ? placeholders).
"""

from datetime import date

from tools import config


def physical_table(table: str) -> str:
    """The real table name to query: the logical name plus the configured prefix
    (e.g. golden_weekly_metrics on Athena). Only names from config are allowed."""
    if table not in config.TABLES:
        raise ValueError(f"Unknown table: {table}")
    return f"{config.TABLE_PREFIX}{table}"


def resolve_column(table: str, name: str) -> str:
    """Map the placeholders 'revenue' and 'orders' to each table's real column name."""
    return config.TABLES[table].get(name, name)


def metric_columns(table: str, metric_def: dict) -> list[str]:
    """Every column a metric needs, resolved for the given table."""
    names = [metric_def["numerator"], metric_def["base"]]
    if "denominator" in metric_def:
        names.append(metric_def["denominator"])
    names += metric_def.get("denominator_sum", [])
    if "event" in metric_def:
        names.append(metric_def["event"])
    if "review_count" in names:
        names.append("orders")  # needed to report review coverage
    return sorted({resolve_column(table, n) for n in names})


def invalid_row_condition(table: str) -> str:
    """SQL condition that is true for a row failing basic sanity checks.
    Checked per stored row, before summing, so one bad row cannot hide inside a total."""
    orders = resolve_column(table, "orders")
    revenue = resolve_column(table, "revenue")
    counts = [orders, revenue, "items", "freight", "canceled_orders", "review_count", "review_sum",
              "one_star_count", "delivered_orders", "late_orders", "delivery_days_sum"]
    checks = [f"{c} < 0" for c in counts] + [
        "late_orders > delivered_orders",
        "one_star_count > review_count",
        f"delivered_orders > {orders}",
        f"review_count > {orders}",
        "(review_count > 0 AND (review_sum < review_count OR review_sum > 5 * review_count))",
    ]
    return "(" + " OR ".join(checks) + ")"


def weekly_series_sql(table: str, columns: list[str], start: date, end: date,
                      filters: dict, group_by: str | None = None,
                      count_groups: bool = False) -> tuple[str, list]:
    """Weekly sums for a range of weeks, optionally split by one dimension.

    Reads only the small summary tables, only the needed columns, and only the
    requested weeks, so every query scans a tiny amount of data.
    """
    if table not in config.TABLES:
        raise ValueError(f"Unknown table: {table}")
    allowed_columns = {"state_group", "category_group"}
    select_cols = ", ".join(f"SUM({c}) AS {c}" for c in columns)
    select_cols += f", SUM(CASE WHEN {invalid_row_condition(table)} THEN 1 ELSE 0 END) AS invalid_rows"
    if count_groups:  # completeness check: how many state groups reported this week
        select_cols += ", COUNT(DISTINCT state_group) AS group_count"
    group_cols = "week_start" + (f", {group_by}" if group_by else "")
    where = ["week_start BETWEEN ? AND ?"]
    params: list = [start, end]
    for col, value in filters.items():
        if col not in allowed_columns:
            raise ValueError(f"Filter not allowed: {col}")
        where.append(f"{col} = ?")
        params.append(value)
    if group_by and group_by not in allowed_columns:
        raise ValueError(f"Group-by not allowed: {group_by}")
    sql = (
        f"SELECT {group_cols}, {select_cols} "
        f"FROM {physical_table(table)} "
        f"WHERE {' AND '.join(where)} "
        f"GROUP BY {group_cols} "
        f"ORDER BY {group_cols}"
    )
    return sql, params