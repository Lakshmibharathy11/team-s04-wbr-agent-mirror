"""Concise views of tool results for the model (context engineering).

The full envelope goes to the evidence log; the model sees only what it needs to
decide its next step, plus just-in-time guidance when a result is partial or failed.
"""

TOP_GROUPS = 6   # breakdown groups shown to the model; the full list stays in the log

GUIDANCE = {
    "error": "This call failed. Do not report any value from it. Add it to 'unavailable'.",
    "partial": "Some values are missing or not final. Say so in any claim that uses this result.",
}


def _series(d: dict) -> dict:
    keep = ("current", "previous", "change", "change_type", "z_score", "z_threshold",
            "is_unusual", "current_base", "current_review_coverage_pct",
            "absolute_change", "share_of_total_change_pct")
    return {k: d[k] for k in keep if k in d and d[k] is not None}


def concise_view(envelope: dict) -> dict:
    view = {
        "request_id": envelope["request_id"],
        "tool": envelope["tool_name"],
        "status": envelope["status"],
        "week": envelope["parameters"].get("week"),
        "compare_week": envelope["parameters"].get("compare_week"),
        "filters": envelope["parameters"].get("filters", {}),
    }
    if envelope["status"] in GUIDANCE:
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
                          for g in groups[:TOP_GROUPS]]
        if len(groups) > TOP_GROUPS:
            view["groups_not_shown"] = len(groups) - TOP_GROUPS
    elif tool == "get_delivery_signals":
        view["metrics"] = {m: _series(v) for m, v in data["metrics"].items()}
    return view
