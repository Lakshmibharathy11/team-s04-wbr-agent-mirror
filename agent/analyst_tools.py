"""Strands tool wrappers around the query tools.

Each wrapper calls the plain function in tools/wbr_tools.py, records the full
envelope in the run's evidence log, and returns a concise view to the model.

The report week is bound to the run: every call uses ctx.week, whatever week the
model passes (a live run once passed the following week in every call). A different
requested week is recorded on the call, so the override is visible.
"""

from strands import tool

from agent.run_context import RunContext
from agent.views import concise_view
from tools import wbr_tools


def make_tools(ctx: RunContext) -> list:
    """Build the three tools bound to one run's context."""

    def _run(name: str, fn, args: dict) -> dict:
        requested = args.get("week")
        args = {**args, "week": ctx.week}            # the run's week, never the model's choice
        envelope = fn(**args)
        ctx.record(name, args, envelope,
                   requested_week=requested if requested not in (None, ctx.week) else None)
        return concise_view(envelope)

    @tool
    def get_weekly_metric(metric: str, week: str | None = None, state: str | None = None,
                          category: str | None = None) -> dict:
        """Check one metric for one week vs the previous week, and whether the change is unusual.

        Use first, to DETECT whether something changed (single-metric threshold |z| >= 2).

        Args:
            metric: One of product_revenue, orders, items, avg_order_value, items_per_order,
                avg_item_price, avg_review_score, one_star_pct, late_delivery_pct,
                avg_delivery_days, cancellation_rate (cancellation_rate: no category filter).
            week: Optional. Always the review week of this run; another value is ignored.
            state: Optional state group: SP, RJ, MG, RS, PR, BA, SC, Other.
            category: Optional category group, one of: bed_bath_table, sports_leisure,
                health_beauty, furniture_decor, computers_accessories, housewares, watches_gifts,
                telephony, cool_stuff, toys, garden_tools, auto, perfumery, baby, Other.
        """
        return _run("get_weekly_metric", wbr_tools.get_weekly_metric,
                    {"metric": metric, "week": week, "state": state, "category": category})

    @tool
    def get_breakdown(metric: str, dimension: str, week: str | None = None, state: str | None = None,
                      category: str | None = None) -> dict:
        """Split one metric by state or category to ATTRIBUTE a change: where did it happen?

        Every group is listed, ordered by z-score (not by size of change). For product_revenue,
        orders, and items, absolute_change is each group's change and share_of_total_change_pct
        its share of the total change. Pick groups by the plan's rule (absolute_change or
        share), not by list position. A group's own is_unusual uses a stricter threshold
        (|z| >= 3) because many groups are tested at once.

        Args:
            metric: Same metrics as get_weekly_metric.
            week: Optional. Always the review week of this run; another value is ignored.
            dimension: "state" or "category".
            state: Optional state filter (only when dimension is "category").
            category: Optional category filter (only when dimension is "state").
        """
        return _run("get_breakdown", wbr_tools.get_breakdown,
                    {"metric": metric, "week": week, "dimension": dimension,
                     "state": state, "category": category})

    @tool
    def get_delivery_signals(week: str | None = None, state: str | None = None,
                             category: str | None = None) -> dict:
        """Late delivery % and average delivery days for a slice, vs the previous week.

        Use to check what ELSE changed in the same place. Shows association only, never cause.

        Args:
            week: Optional. Always the review week of this run; another value is ignored.
            state: Optional state group.
            category: Optional category group.
        """
        return _run("get_delivery_signals", wbr_tools.get_delivery_signals,
                    {"week": week, "state": state, "category": category})

    return [get_weekly_metric, get_breakdown, get_delivery_signals]
