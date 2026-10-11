"""The query tools the analyst agent calls. Each returns the standard envelope
(tools/envelope.py) and never raises: every failure becomes status 'error'."""

import statistics
from datetime import date, timedelta

from tools import config
from tools.backend import QueryError, run_query
from tools.envelope import (make_envelope, make_error, make_evidence_ref,
                            make_warning, to_number)
from tools.queries import metric_columns, physical_table, resolve_column, weekly_series_sql

ONE_WEEK = timedelta(days=7)


class ValidationError(Exception):
    """An input outside the allowed values; reported as status 'error'."""


# ---------------------------------------------------------------------------
# Input checks
# ---------------------------------------------------------------------------
def _parse_week(week) -> date:
    """Weeks are ISO dates on a Monday, inside the window or a known incomplete week."""
    try:
        parsed = week if isinstance(week, date) else date.fromisoformat(str(week))
    except ValueError:
        raise ValidationError(f"Week must be a date like 2018-06-04, got {week!r}")
    if parsed.weekday() != 0:
        raise ValidationError(f"Week must start on a Monday, got {parsed} ({parsed:%A})")
    in_window = config.WINDOW_START + ONE_WEEK <= parsed <= config.WINDOW_END
    if not (in_window or parsed in config.INCOMPLETE_WEEKS):
        raise ValidationError(
            f"Week {parsed} is outside the analysis window. Use a Monday from "
            f"{config.WINDOW_START + ONE_WEEK} to {config.WINDOW_END}."
        )
    return parsed


def _get_metric_def(metric: str, table: str) -> dict:
    if metric in config.METRICS:
        return config.METRICS[metric]
    if metric in config.STATE_ONLY_METRICS:
        if table != "weekly_metrics":
            raise ValidationError(f"{metric} is only available without a category filter")
        return config.STATE_ONLY_METRICS[metric]
    allowed = sorted(config.METRICS) + sorted(config.STATE_ONLY_METRICS)
    raise ValidationError(f"Unknown metric {metric!r}. Allowed: {', '.join(allowed)}")


def _check_value(value, allowed: list, label: str):
    if value is not None and value not in allowed:
        raise ValidationError(f"Unknown {label} {value!r}. Allowed: {', '.join(allowed)}")


# ---------------------------------------------------------------------------
# Calculations (done in Python from sums, never by the model)
# ---------------------------------------------------------------------------
def _metric_value(row: dict | None, metric_def: dict, table: str):
    """Value of a metric for one week's sums. None if missing or denominator is 0."""
    if row is None:
        return None
    numerator = to_number(row.get(resolve_column(table, metric_def["numerator"])))
    if numerator is None:
        return None
    if metric_def["kind"] == "sum":
        return numerator
    if "denominator_sum" in metric_def:
        parts = [to_number(row.get(resolve_column(table, c))) for c in metric_def["denominator_sum"]]
        denominator = None if None in parts else sum(parts)
    else:
        denominator = to_number(row.get(resolve_column(table, metric_def["denominator"])))
    if not denominator:
        return None
    return numerator / denominator * metric_def.get("scale", 1)


def _change(current, previous, change_type: str):
    """Percent change or point difference. None when it cannot be calculated."""
    if current is None or previous is None:
        return None
    if change_type == "points":
        return current - previous
    if previous == 0:
        return None
    return (current - previous) / abs(previous) * 100


def _z_score(current_change, baseline_changes: list):
    """How unusual this week's change is versus the group's own recent changes.

    Robust method (default): center = median, spread = 1.4826 x MAD, so one or
    two extreme weeks cannot inflate the spread and hide a real change.
    Returns (center, spread, z); z is None when it cannot be calculated.
    """
    if current_change is None or len(baseline_changes) < config.BASELINE_WEEKS:
        return None, None, None
    if config.Z_METHOD == "robust":
        center = statistics.median(baseline_changes)
        mad = statistics.median(abs(c - center) for c in baseline_changes)
        spread = config.MAD_SCALE * mad
    else:
        center = statistics.mean(baseline_changes)
        spread = statistics.stdev(baseline_changes)
    if spread == 0:
        return center, spread, None
    return center, spread, (current_change - center) / spread


def _round(value, digits=None):
    digits = config.NUMERIC_PRECISION_DIGITS if digits is None else digits
    if value is None:
        return None
    rounded = round(value, digits)
    return 0.0 if rounded == 0 else rounded  # avoid showing -0.0


def _clean_rows(rows_by_week: dict, warnings: list, label: str,
                expected_groups: int | None = None) -> tuple[dict, set, set]:
    """Drop weeks that contain rows failing sanity checks, or (for totals) weeks
    where some state groups are missing, so bad data never becomes a number."""
    clean, invalid, incomplete = {}, set(), set()
    for w, row in rows_by_week.items():
        if (to_number(row.get("invalid_rows")) or 0) > 0:
            invalid.add(w)
        elif expected_groups and (to_number(row.get("group_count")) or 0) < expected_groups:
            incomplete.add(w)
        else:
            clean[w] = row
    if invalid:
        warnings.append(make_warning("INVALID_DATA", f"{label}: rows failing sanity checks excluded",
                                     weeks=sorted(invalid)))
    # Team acceptance rule: any week with missing state groups, compared or baseline, raises
    # NO_DATA (which makes the result partial). Excluded weeks are also listed in
    # baseline.excluded_weeks so the reason is visible.
    if incomplete:
        warnings.append(make_warning("NO_DATA", f"{label}: some state groups missing; week excluded",
                                     weeks=sorted(incomplete)))
    return clean, invalid, incomplete


def _summarize_series(rows_by_week: dict, week: date, metric: str, metric_def: dict,
                      table: str, warnings: list, label: str,
                      expected_groups: int | None = None,
                      threshold: float | None = None) -> dict:
    """Current vs previous week, plus the z-score baseline, for one series."""
    if threshold is None:
        threshold = config.Z_THRESHOLD_SINGLE
    rows_by_week, invalid, incomplete = _clean_rows(rows_by_week, warnings, label, expected_groups)
    change_type = metric_def["change"]
    weeks = [week - ONE_WEEK * i for i in range(config.BASELINE_WEEKS + 1, -1, -1)]
    values = {w: _metric_value(rows_by_week.get(w), metric_def, table) for w in weeks}

    previous_week = week - ONE_WEEK
    current, previous = values[week], values[previous_week]
    change = _change(current, previous, change_type)

    # Baseline: changes between earlier consecutive weeks, inside the window only
    baseline = []
    for earlier, later in zip(weeks[:-2], weeks[1:-1]):
        if earlier >= config.WINDOW_START:
            c = _change(values[earlier], values[later], change_type)
            if c is not None:
                baseline.append(c)
    mean, std, z = _z_score(change, baseline)

    base_col = resolve_column(table, metric_def["base"])
    current_row, previous_row = rows_by_week.get(week), rows_by_week.get(previous_week)
    current_base = to_number(current_row.get(base_col)) if current_row else 0
    previous_base = to_number(previous_row.get(base_col)) if previous_row else 0

    # Warnings
    if (current_row is None or previous_row is None) and not ({week, previous_week} & (invalid | incomplete)):
        warnings.append(make_warning("NO_DATA", f"{label}: no rows for one of the compared weeks"))
    if change is None and current_row is not None and previous_row is not None:
        warnings.append(make_warning("ZERO_DENOMINATOR", f"{label}: change cannot be calculated (zero base)"))
    small_base = min(current_base or 0, previous_base or 0)
    if small_base < config.MIN_ORDERS:
        warnings.append(make_warning("SMALL_SAMPLE", f"{label}: only {small_base} in the base ({base_col})",
                                     base=small_base, minimum=config.MIN_ORDERS))
    if "event" in metric_def:
        event_col = resolve_column(table, metric_def["event"])
        events = min(to_number(current_row.get(event_col)) if current_row else 0,
                     to_number(previous_row.get(event_col)) if previous_row else 0)
        if (events or 0) < config.MIN_EVENTS:
            warnings.append(make_warning("SMALL_SAMPLE", f"{label}: only {events} events ({event_col})",
                                         events=events, minimum=config.MIN_EVENTS))
    if z is None and change is not None and len(baseline) < config.BASELINE_WEEKS:
        warnings.append(make_warning("INSUFFICIENT_HISTORY",
                                     f"{label}: {len(baseline)} earlier weeks available; "
                                     f"{config.BASELINE_WEEKS} needed for a z-score"))

    result = {
        "current": _round(current),
        "previous": _round(previous),
        "change": _round(change),
        "change_type": change_type,
        "current_base": current_base,
        "previous_base": previous_base,
        "baseline": {"weeks": len(baseline), "method": config.Z_METHOD,
                     "center_change": _round(mean), "spread_change": _round(std)},
        "z_score": _round(z),
        "z_threshold": threshold,
        "is_unusual": None if z is None else abs(z) >= threshold,
    }
    compared = {week, previous_week}
    excluded = sorted((invalid | incomplete) - compared)
    if excluded:
        result["baseline"]["excluded_weeks"] = excluded
    if compared & invalid:
        result["data_issue"] = "invalid"
    elif compared & incomplete:
        result["data_issue"] = "incomplete"
    # Review metrics: report coverage (share of orders with a review)
    if metric in ("avg_review_score", "one_star_pct"):
        orders_col = resolve_column(table, "orders")
        for key, row in (("current", current_row), ("previous", previous_row)):
            orders = to_number(row.get(orders_col)) if row else None
            reviews = to_number(row.get("review_count")) if row else None
            result[f"{key}_review_coverage_pct"] = _round(reviews / orders * 100) if orders else None
    return result


def _status(warnings: list, week: date, data_ok: bool) -> str:
    if not data_ok:
        return "partial"
    blocking = {"NO_DATA", "ZERO_DENOMINATOR", "INCOMPLETE_WEEK"}
    return "partial" if any(w["code"] in blocking for w in warnings) else "ok"


# ---------------------------------------------------------------------------
# Tool 1: get_weekly_metric
# ---------------------------------------------------------------------------
def get_weekly_metric(metric: str, week: str, state: str | None = None,
                      category: str | None = None) -> dict:
    """One metric for one week vs the previous week, with a z-score.

    Totals (no category) read weekly_metrics; with a category filter, the
    category table. Optional state and category filters narrow the slice.
    """
    tool = "get_weekly_metric"
    parameters = {"metric": metric, "week": week, "state": state, "category": category}
    try:
        _check_value(state, config.STATE_GROUPS, "state")
        _check_value(category, config.CATEGORY_GROUPS, "category")
        week_date = _parse_week(week)
        table = "weekly_category_metrics" if category else "weekly_metrics"
        metric_def = _get_metric_def(metric, table)
    except ValidationError as exc:
        return make_envelope(tool, "error", parameters, error=make_error("validation", str(exc)))

    filters = {}
    if state:
        filters["state_group"] = state
    if category:
        filters["category_group"] = category
    start = week_date - ONE_WEEK * (config.BASELINE_WEEKS + 1)
    parameters.update({
        "week": week_date, "compare_week": week_date - ONE_WEEK,
        "grain": config.TABLES[table]["grain"], "filters": filters,
        "numeric_precision_digits": config.NUMERIC_PRECISION_DIGITS,
        "baseline_from": max(start, config.WINDOW_START),
    })

    query_id = f"weekly_metric_{metric}_{config.QUERY_VERSION}"
    is_total = table == "weekly_metrics" and not state
    sql, params = weekly_series_sql(table, metric_columns(table, metric_def), start, week_date,
                                    filters, count_groups=is_total)
    try:
        rows, meta = run_query(query_id, sql, params)
    except QueryError as exc:
        return make_envelope(tool, "error", parameters, error=make_error(exc.error_type, exc.message))

    try:
        warnings = []
        if week_date in config.INCOMPLETE_WEEKS:
            warnings.append(make_warning("INCOMPLETE_WEEK", f"Week {week_date} has incomplete data; numbers are not final"))
        rows_by_week = {r["week_start"]: r for r in rows}
        summary = _summarize_series(rows_by_week, week_date, metric, metric_def, table, warnings, "total",
                                    expected_groups=len(config.STATE_GROUPS) if is_total else None)
    except (TypeError, ValueError, KeyError) as exc:
        return make_envelope(tool, "error", parameters, error=make_error("malformed_data", str(exc)))
    if summary.get("data_issue") == "invalid":
        return make_envelope(tool, "error", parameters, warnings=warnings, error=make_error(
            "malformed_data", "Rows for the compared weeks failed sanity checks; no values reported"))
    data = {"metric": metric, **summary}
    evidence = [make_evidence_ref(query_id, meta["backend"], physical_table(table), len(rows), meta["executed_at"],
                                  meta.get("athena_query_execution_id"))]
    return make_envelope(tool, _status(warnings, week_date, summary["current"] is not None),
                         parameters, data=data, evidence_refs=evidence, warnings=warnings)


# ---------------------------------------------------------------------------
# Tool 2: get_breakdown
# ---------------------------------------------------------------------------
def _sort_key(group: dict):
    """Biggest movers first: by |z| when available, else by |change|; missing values last."""
    z, change = group.get("z_score"), group.get("change")
    if z is not None:
        return (0, -abs(z))
    if change is not None:
        return (1, -abs(change))
    return (2, 0)


def get_breakdown(metric: str, week: str, dimension: str, state: str | None = None,
                  category: str | None = None) -> dict:
    """One metric split by state or category, each group vs the previous week,
    with z-scores, sorted biggest movers first.

    For additive metrics (revenue, orders, items) each group also gets its share
    of the total change, showing which groups drove it.
    """
    tool = "get_breakdown"
    parameters = {"metric": metric, "week": week, "dimension": dimension,
                  "state": state, "category": category}
    try:
        if dimension not in config.DIMENSIONS:
            raise ValidationError(f"Unknown dimension {dimension!r}. Allowed: state, category")
        if dimension == "state" and state:
            raise ValidationError("Do not filter by state when breaking down by state")
        if dimension == "category" and category:
            raise ValidationError("Do not filter by category when breaking down by category")
        _check_value(state, config.STATE_GROUPS, "state")
        _check_value(category, config.CATEGORY_GROUPS, "category")
        week_date = _parse_week(week)
        table = "weekly_category_metrics" if (dimension == "category" or category) else "weekly_metrics"
        metric_def = _get_metric_def(metric, table)
    except ValidationError as exc:
        return make_envelope(tool, "error", parameters, error=make_error("validation", str(exc)))

    group_col = config.DIMENSIONS[dimension]["column"]
    filters = {}
    if state:
        filters["state_group"] = state
    if category:
        filters["category_group"] = category
    start = week_date - ONE_WEEK * (config.BASELINE_WEEKS + 1)
    parameters.update({
        "week": week_date, "compare_week": week_date - ONE_WEEK,
        "grain": config.TABLES[table]["grain"], "filters": filters,
        "numeric_precision_digits": config.NUMERIC_PRECISION_DIGITS,
        "baseline_from": max(start, config.WINDOW_START),
    })

    query_id = f"breakdown_{dimension}_{metric}_{config.QUERY_VERSION}"
    sql, params = weekly_series_sql(table, metric_columns(table, metric_def), start, week_date,
                                    filters, group_by=group_col)
    try:
        rows, meta = run_query(query_id, sql, params)
    except QueryError as exc:
        return make_envelope(tool, "error", parameters, error=make_error(exc.error_type, exc.message))

    warnings = []
    if week_date in config.INCOMPLETE_WEEKS:
        warnings.append(make_warning("INCOMPLETE_WEEK", f"Week {week_date} has incomplete data; numbers are not final"))

    try:
        groups = _breakdown_groups(rows, week_date, metric, metric_def, table, dimension, group_col, warnings)
    except (TypeError, ValueError, KeyError) as exc:
        return make_envelope(tool, "error", parameters, error=make_error("malformed_data", str(exc)))

    groups.sort(key=_sort_key)
    data = {"metric": metric, "dimension": dimension, "groups": groups}
    has_data = any(g["current"] is not None for g in groups)
    bad_group = any(g.get("data_issue") for g in groups)
    evidence = [make_evidence_ref(query_id, meta["backend"], physical_table(table), len(rows), meta["executed_at"],
                                  meta.get("athena_query_execution_id"))]
    status = "partial" if (not has_data or bad_group or week_date in config.INCOMPLETE_WEEKS) else "ok"
    return make_envelope(tool, status, parameters, data=data, evidence_refs=evidence, warnings=warnings)


def _breakdown_groups(rows, week_date, metric, metric_def, table, dimension, group_col, warnings):
    groups = []
    for group in config.DIMENSIONS[dimension]["allowed"]:
        rows_by_week = {r["week_start"]: r for r in rows if r[group_col] == group}
        group_warnings = []
        summary = _summarize_series(rows_by_week, week_date, metric, metric_def, table,
                                    group_warnings, group,
                                    threshold=config.Z_THRESHOLD_BREAKDOWN)
        groups.append({"group": group, **summary,
                       "warnings": [w["code"] for w in group_warnings]})
        warnings.extend(group_warnings)

    # Share of the total change, for additive metrics only
    if metric_def["kind"] == "sum":
        deltas = {g["group"]: (g["current"] - g["previous"])
                  for g in groups if g["current"] is not None and g["previous"] is not None}
        total_delta = sum(deltas.values())
        for g in groups:
            delta = deltas.get(g["group"])
            g["absolute_change"] = _round(delta)
            g["share_of_total_change_pct"] = (
                _round(delta / total_delta * 100) if delta is not None and total_delta else None
            )
    return groups


# ---------------------------------------------------------------------------
# Tool 3: get_delivery_signals
# ---------------------------------------------------------------------------
def get_delivery_signals(week: str, state: str | None = None, category: str | None = None) -> dict:
    """Delivery signals for a slice: late delivery % and average delivery days,
    this week vs the previous week, with z-scores.

    Used to check whether a review or revenue change lines up with delivery
    problems. It shows association only, never cause.
    """
    tool = "get_delivery_signals"
    parameters = {"week": week, "state": state, "category": category}
    try:
        _check_value(state, config.STATE_GROUPS, "state")
        _check_value(category, config.CATEGORY_GROUPS, "category")
        week_date = _parse_week(week)
    except ValidationError as exc:
        return make_envelope(tool, "error", parameters, error=make_error("validation", str(exc)))

    table = "weekly_category_metrics" if category else "weekly_metrics"
    filters = {}
    if state:
        filters["state_group"] = state
    if category:
        filters["category_group"] = category
    start = week_date - ONE_WEEK * (config.BASELINE_WEEKS + 1)
    parameters.update({
        "week": week_date, "compare_week": week_date - ONE_WEEK,
        "grain": config.TABLES[table]["grain"], "filters": filters,
        "numeric_precision_digits": config.NUMERIC_PRECISION_DIGITS,
        "baseline_from": max(start, config.WINDOW_START),
    })

    columns = sorted({c for m in config.DELIVERY_SIGNAL_METRICS
                      for c in metric_columns(table, config.METRICS[m])})
    query_id = f"delivery_signals_{config.QUERY_VERSION}"
    sql, params = weekly_series_sql(table, columns, start, week_date, filters,
                                    count_groups=(table == "weekly_metrics" and not state))
    try:
        rows, meta = run_query(query_id, sql, params)
    except QueryError as exc:
        return make_envelope(tool, "error", parameters, error=make_error(exc.error_type, exc.message))

    warnings = []
    if week_date in config.INCOMPLETE_WEEKS:
        warnings.append(make_warning("INCOMPLETE_WEEK", f"Week {week_date} has incomplete data; numbers are not final"))
    is_total = table == "weekly_metrics" and not state
    try:
        rows_by_week = {r["week_start"]: r for r in rows}
        metrics = {m: _summarize_series(rows_by_week, week_date, m, config.METRICS[m], table, warnings, m,
                                        expected_groups=len(config.STATE_GROUPS) if is_total else None)
                   for m in config.DELIVERY_SIGNAL_METRICS}
    except (TypeError, ValueError, KeyError) as exc:
        return make_envelope(tool, "error", parameters, error=make_error("malformed_data", str(exc)))
    if any(v.get("data_issue") == "invalid" for v in metrics.values()):
        return make_envelope(tool, "error", parameters, warnings=warnings, error=make_error(
            "malformed_data", "Rows for the compared weeks failed sanity checks; no values reported"))
    data = {"metrics": metrics,
            "note": "Association only: delivery changes alongside other changes do not establish cause."}
    has_data = any(v["current"] is not None for v in metrics.values())
    evidence = [make_evidence_ref(query_id, meta["backend"], physical_table(table), len(rows), meta["executed_at"],
                                  meta.get("athena_query_execution_id"))]
    return make_envelope(tool, _status(warnings, week_date, has_data), parameters,
                         data=data, evidence_refs=evidence, warnings=warnings)
