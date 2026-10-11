"""Conservative contradiction check for an explicitly bounded claim grammar.

Never accepts arbitrary prose, and never promotes a claim solely because a
number appears somewhere in a capture. Unknown forms retain fallback review.
"""
import math
import re
from collections.abc import Mapping
from decimal import Decimal
from datetime import date, timedelta

from .wbr_runner import default_verify_claim


CHANGE_CLAIM = re.compile(
    r"(?:(?P<category>[a-z][a-z0-9_]*) )?"
    r"(?P<metric>revenue|orders|items) (?P<direction>fell|rose) "
    r"(?P<amount>\d+(?:\.\d+)?)%"
    r"(?: in (?P<state>[A-Z]{2}|Other))?\.?", re.ASCII)
METRICS = {"revenue": "product_revenue", "orders": "orders", "items": "items"}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _series(envelope, metric, state, category, week):
    """Select precisely the stated metric, comparison week and filter slice."""
    params, data = envelope.get("parameters"), envelope.get("data")
    if not isinstance(params, Mapping) or not isinstance(data, Mapping):
        return []
    if params.get("week") != week or params.get("metric") != metric or data.get("metric") != metric:
        return []
    if envelope.get("tool_name") == "get_weekly_metric":
        if params.get("state") == state and params.get("category") == category:
            return [data]
    if envelope.get("tool_name") == "get_breakdown":
        dimension = params.get("dimension")
        if data.get("dimension") != dimension:
            return []
        group = None
        if dimension == "state" and state and params.get("state") is None and params.get("category") == category:
            group = state
        if dimension == "category" and category and params.get("category") is None and params.get("state") == state:
            group = category
        groups = data.get("groups")
        if group and isinstance(groups, list):
            return [g for g in groups if isinstance(g, Mapping) and g.get("group") == group]
    return []


def make_captured_verifier(week, *, allow_verified_facts=False):
    """Remove contradictions with scoped, internally consistent numeric evidence.

    Only positive previous values and finite non-negative additive metrics are
    supported. The claimed percentage is rounded at its displayed precision.
    Matching numbers remain subject to the existing semantic/human review;
    the default never upgrades the fallback outcome to keep. A reviewed,
    explicit opt-in may keep only fully bounded facts with reconciled quality
    and confidence. Enabling it is not itself release approval.
    """
    def verify(claim, evidence):
        fallback = default_verify_claim(claim, evidence)
        if fallback["outcome"] == "remove" or fallback["reason"] == "unsupported causal language":
            return fallback
        match = CHANGE_CLAIM.fullmatch(claim["statement"])
        if not match or claim["claim_type"] != "fact":
            return fallback
        metric = METRICS[match["metric"]]
        series = []
        captures = []
        for ref in set(claim["supporting_evidence"]):
            envelope = evidence[ref]
            if envelope.get("status") not in {"ok", "partial"}:
                continue
            envelope = envelope.get("source_envelope", envelope)
            selected = _series(envelope, metric, match["state"], match["category"], week)
            series.extend(selected)
            if selected:
                captures.append(envelope)
        if not series:
            return fallback
        intervals = []
        for item in series:
            current, previous, change = (item.get(k) for k in ("current", "previous", "change"))
            if (item.get("change_type") != "pct" or item.get("data_issue")
                    or not all(_finite(v) for v in (current, previous, change))
                    or current < 0 or previous <= 0):
                return {**fallback, "reason": "numeric evidence incomplete or malformed"}
            # Lakshmi's builder rounds current, previous and change separately
            # to two decimals. Recomputing rounded inputs with an exact-value
            # tolerance would falsely reject legitimate captures. Intersect
            # the possible arithmetic range with the reported-change range.
            c, p, delta = (Decimal(str(v)) for v in (current, previous, change))
            half = Decimal("0.005")
            if p <= half:
                return {**fallback, "reason": "numeric rounding leaves denominator uncertain"}
            lower = (max(Decimal(0), c - half) / (p + half) - 1) * 100
            upper = ((c + half) / (p - half) - 1) * 100
            lower, upper = max(lower, delta - half), min(upper, delta + half)
            if lower > upper + Decimal("0.000001"):
                return {**fallback, "reason": "numeric evidence is internally inconsistent"}
            intervals.append((lower, upper))
        lower = max(i[0] for i in intervals)
        upper = min(i[1] for i in intervals)
        if lower > upper + Decimal("0.000001"):
            return {**fallback, "reason": "conflicting numeric captures require review"}
        amount = Decimal(match["amount"])
        if amount < 0 or amount > 1000000:
            return fallback
        signed = -amount if match["direction"] == "fell" else amount
        half_unit = Decimal(1).scaleb(amount.as_tuple().exponent) / 2
        # Boundary ties are allowed conservatively; signed direction is checked
        # independently so a tiny rise cannot validate a rounded drop.
        direction_impossible = lower >= 0 if match["direction"] == "fell" else upper <= 0
        epsilon = Decimal("0.000001")
        if (direction_impossible or upper < signed - half_unit - epsilon
                or lower > signed + half_unit + epsilon):
            return {"claim_id": claim["claim_id"], "outcome": "remove",
                    "reason": "claimed percentage contradicts scoped captured evidence"}
        if allow_verified_facts and not claim["requires_human_review"]:
            # A partially overlapping rounding interval is insufficient proof
            # for keep. Every possible captured value must fit the assertion.
            certain_direction = upper < 0 if match["direction"] == "fell" else lower > 0
            contained = lower >= signed - half_unit and upper <= signed + half_unit
            quality, confidence = _quality_and_confidence(series, captures, week)
            all_citations_scoped = len(captures) == len(set(claim["supporting_evidence"]))
            if (certain_direction and contained and quality and all_citations_scoped
                    and claim["confidence"] == confidence
                    and claim["causal_language_allowed"] is False):
                return {"claim_id": claim["claim_id"], "outcome": "keep",
                        "reason": "bounded numeric fact and scoped confidence verified"}
        return {**fallback, "reason": "numeric percentage matches; semantic/confidence review remains"}
    return verify


def _quality_and_confidence(series, captures, week):
    """Conservative proposed rule; never borrow another slice's strongest z."""
    try:
        requested = date.fromisoformat(week)
        if requested.isoformat() != week or requested.weekday() != 0:
            return False, None
        comparison = (requested - timedelta(days=7)).isoformat()
    except (TypeError, ValueError):
        return False, None
    for capture in captures:
        params = capture["parameters"]
        if params.get("compare_week") != comparison:
            return False, None
        if capture.get("status") != "ok" or not isinstance(capture.get("warnings"), list):
            return False, None
        if capture["warnings"]:
            return False, None
    z_scores = []
    for item in series:
        if item.get("warnings") or item.get("data_issue"):
            return False, None
        for key in ("current_base", "previous_base"):
            value = item.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 30:
                return False, None
        baseline = item.get("baseline")
        if not isinstance(baseline, Mapping):
            return False, None
        weeks = baseline.get("weeks")
        if isinstance(weeks, bool) or not isinstance(weeks, int) or weeks < 8:
            return False, None
        if not _finite(item.get("z_score")):
            return False, None
        z_scores.append(abs(item["z_score"]))
    return True, "high" if max(z_scores) >= 3 else "medium"
