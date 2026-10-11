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
from tools import config


CHANGE_CLAIM = re.compile(
    r"(?:(?P<category>[a-z][a-z0-9_]*) )?"
    r"(?P<metric>revenue|orders|items) (?P<direction>fell|rose) "
    r"(?P<amount>\d+(?:\.\d+)?)%"
    r"(?: in (?P<state>[A-Z]{2}|Other))?\.?", re.ASCII)
METRICS = {"revenue": "product_revenue", "orders": "orders", "items": "items"}
REVIEW_CHANGE_CLAIM = re.compile(
    r"(?:(?P<category>[a-z][a-z0-9_]*) )?average review score "
    r"(?P<direction>fell|rose) (?P<amount>\d+(?:\.\d+)?) points"
    r"(?: in (?P<state>[A-Z]{2}|Other))?\.?", re.ASCII)


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _series(envelope, metric, state, category, week):
    """Select precisely the stated metric, comparison week and filter slice."""
    params, data = envelope.get("parameters"), envelope.get("data")
    if not isinstance(params, Mapping) or not isinstance(data, Mapping):
        return []
    expected_filters = {k: v for k, v in (("state_group", params.get("state")),
                                               ("category_group", params.get("category"))) if v is not None}
    if "filters" in params and params["filters"] != expected_filters:
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
        review_match = REVIEW_CHANGE_CLAIM.fullmatch(claim["statement"])
        if review_match and claim["claim_type"] == "fact":
            return _verify_review_change(claim, evidence, review_match, week,
                                         fallback, allow_verified_facts)
        match = CHANGE_CLAIM.fullmatch(claim["statement"])
        if not match or claim["claim_type"] != "fact":
            return fallback
        if match["metric"] == "orders" and match["category"]:
            return {**fallback, "reason": "category orders_with_category wording requires review"}
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
        if _precision_mismatch(captures):
            return {**fallback, "reason": "capture precision differs from verifier; review required"}
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
            half = Decimal(1).scaleb(-config.NUMERIC_PRECISION_DIGITS) / 2
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
        if amount == 0:
            return {"claim_id": claim["claim_id"], "outcome": "remove",
                    "reason": "zero magnitude cannot state a rise or fall"}
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


def _verify_review_change(claim, evidence, match, week, fallback, allow_verified_facts):
    """Only a bounded difference of mean review scores, never unusualness or cause."""
    series, captures = [], []
    for ref in set(claim["supporting_evidence"]):
        env = evidence[ref]
        if env.get("status") not in {"ok", "partial"}:
            continue
        env = env.get("source_envelope", env)
        selected = _series(env, "avg_review_score", match["state"], match["category"], week)
        series.extend(selected)
        if selected:
            captures.append(env)
    if not series:
        return fallback
    if _precision_mismatch(captures):
        return {**fallback, "reason": "capture precision differs from verifier; review required"}
    intervals = []
    half = Decimal(1).scaleb(-config.NUMERIC_PRECISION_DIGITS) / 2
    for item in series:
        current, previous, change = (item.get(k) for k in ("current", "previous", "change"))
        if (item.get("change_type") != "points" or item.get("data_issue")
                or not all(_finite(v) for v in (current, previous, change))
                or not (1 <= current <= 5 and 1 <= previous <= 5)):
            return {**fallback, "reason": "review-score evidence incomplete or malformed"}
        c, p, delta = (Decimal(str(v)) for v in (current, previous, change))
        lower = max(max(Decimal(1), c-half) - min(Decimal(5), p+half), delta-half)
        upper = min(min(Decimal(5), c+half) - max(Decimal(1), p-half), delta+half)
        if lower > upper:
            return {**fallback, "reason": "review-score evidence is internally inconsistent"}
        intervals.append((lower, upper))
    lower, upper = max(i[0] for i in intervals), min(i[1] for i in intervals)
    if lower > upper:
        return {**fallback, "reason": "conflicting review-score captures require review"}
    amount = Decimal(match["amount"])
    if amount == 0:
        return {"claim_id": claim["claim_id"], "outcome": "remove",
                "reason": "zero magnitude cannot state a rise or fall"}
    if amount > 4:
        return fallback
    signed = -amount if match["direction"] == "fell" else amount
    half_unit = Decimal(1).scaleb(amount.as_tuple().exponent) / 2
    direction_impossible = lower >= 0 if match["direction"] == "fell" else upper <= 0
    if direction_impossible or upper < signed-half_unit or lower > signed+half_unit:
        return {"claim_id": claim["claim_id"], "outcome": "remove",
                "reason": "claimed point change contradicts scoped captured evidence"}
    certain_direction = upper < 0 if match["direction"] == "fell" else lower > 0
    quality, confidence = _quality_and_confidence(series, captures, week)
    if (allow_verified_facts and not claim["requires_human_review"]
            and certain_direction and lower >= signed-half_unit and upper <= signed+half_unit
            and quality and len(captures) == len(set(claim["supporting_evidence"]))
            and claim["confidence"] == confidence and claim["causal_language_allowed"] is False):
        return {"claim_id": claim["claim_id"], "outcome": "keep",
                "reason": "bounded review-score point change and scoped confidence verified"}
    return {**fallback, "reason": "review-score point change matches; semantic/confidence review remains"}


def _precision_mismatch(captures):
    for capture in captures:
        params = capture["parameters"]
        if "numeric_precision_digits" in params:
            digits = params["numeric_precision_digits"]
            if type(digits) is not int or digits != config.NUMERIC_PRECISION_DIGITS:
                return True
    return False


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
        if (type(params.get("numeric_precision_digits")) is not int
                or params["numeric_precision_digits"] != config.NUMERIC_PRECISION_DIGITS
                or "filters" not in params):
            return False, None
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
            if isinstance(value, bool) or not isinstance(value, int) or value < config.MIN_ORDERS:
                return False, None
        baseline = item.get("baseline")
        if not isinstance(baseline, Mapping):
            return False, None
        weeks = baseline.get("weeks")
        if isinstance(weeks, bool) or not isinstance(weeks, int) or weeks < config.BASELINE_WEEKS:
            return False, None
        if not _finite(item.get("z_score")):
            return False, None
        z_scores.append(abs(item["z_score"]))
    return True, "high" if max(z_scores) >= 3 else "medium"
