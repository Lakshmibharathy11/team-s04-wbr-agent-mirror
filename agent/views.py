"""Concise views of tool results for the model (context engineering).

The full envelope goes to the evidence log; the model sees only what it needs to
decide its next step, plus just-in-time guidance when a result is partial or failed.
"""

# Every breakdown group is shown (at most 15 categories or 8 states, bounded by
# config), so the model can find any extreme the plan asks for: the largest share,
# the most negative absolute_change, or the largest change. Ranking only the top few
# by z-score could hide the right group.

GUIDANCE = {
    "error": "This call failed. Do not report any value from it. Add it to 'unavailable'.",
    "partial": "Some values are missing or not final. Say so in any claim that uses this result.",
}
# An invalid input is the agent's own mistake, not missing data: fix it and call again
VALIDATION_GUIDANCE = ("The input was invalid. Fix it using the error message and call again. "
                       "Do not report any value from this call or cite it.")


def _series(d: dict) -> dict:
    """The fields the model needs. A field the tool returned as null stays null, so a
    missing value (e.g. no z-score without enough history) reads as unknown, never as
    absent or false."""
    keep = ("current", "previous", "change", "change_type", "z_score", "z_threshold",
            "is_unusual", "current_base", "current_review_coverage_pct",
            "absolute_change", "share_of_total_change_pct")
    view = {k: d[k] for k in keep if k in d}
    if "is_unusual" in view and view["is_unusual"] is None:
        view["anomaly_check"] = "unavailable"   # explicit: neither normal nor unusual
    return view


def concise_view(envelope: dict) -> dict:
    view = {
        "request_id": envelope["request_id"],
        "tool": envelope["tool_name"],
        "status": envelope["status"],
        "week": envelope["parameters"].get("week"),
        "compare_week": envelope["parameters"].get("compare_week"),
        "filters": envelope["parameters"].get("filters", {}),
    }
    if (envelope.get("error") or {}).get("type") == "validation":
        view["guidance"] = VALIDATION_GUIDANCE
    elif envelope["status"] in GUIDANCE:
        view["guidance"] = GUIDANCE[envelope["status"]]
    if envelope["error"]:
        view["error"] = envelope["error"]["message"]
    codes = {w["code"] for w in envelope.get("warnings", [])}
    if envelope["tool_name"] == "get_breakdown":
        codes &= {"INCOMPLETE_WEEK"}   # group warnings are shown on each group instead
    if codes:
        view["warnings"] = sorted(codes)

    data = envelope.get("data")
    if not data:
        return view
    tool = envelope["tool_name"]
    if tool == "get_weekly_metric":
        view["metric"] = data["metric"]
        view.update(_series(data))
    elif tool == "get_breakdown":
        view["metric"], view["dimension"] = data["metric"], data["dimension"]
        groups = data["groups"]
        view["groups"] = [{"group": g["group"], **_series(g),
                           **({"warnings": g["warnings"]} if g.get("warnings") else {})}
                          for g in groups]
    elif tool == "get_delivery_signals":
        view["metrics"] = {m: _series(v) for m, v in data["metrics"].items()}
    return view
