"""Graders: score an analyst run against the golden answer keys.

Code-based and outcome-focused: they check what the report concludes (the right
state, category, claim type, status), not the exact sequence of tool calls, since
agents can reach a correct answer by different valid paths. Light process checks
(budget, repeated calls) are reported separately and do not decide pass/fail.
"""

import re

NAMES = {   # how a state may be written in a statement
    "SP": ["SP", "São Paulo", "Sao Paulo"],
    "RJ": ["RJ", "Rio de Janeiro"],
    "MG": ["MG", "Minas Gerais"],
    "BA": ["BA", "Bahia"],
}
HONESTY_ISSUES = {"unknown_evidence", "no_valid_evidence", "cites_failed_call", "causal_language"}


def mentions(text: str, name: str) -> bool:
    """Whole-word, case-sensitive for state codes; case-insensitive for full names and categories."""
    for alias in NAMES.get(name, [name]):
        if alias.isupper() and len(alias) == 2:
            if re.search(rf"\b{alias}\b", text):
                return True
        else:
            words = re.split(r"[_\s&]+", alias.lower())
            pattern = r"[\s_&-]*".join(map(re.escape, words))
            if re.search(pattern, text.lower()):
                return True
    return False


def _claims(report):
    return report["claims"] if report else []


def _issues(report, claim_ids=None):
    issues = report["precheck_issues"] if report else []
    return [i for i in issues if claim_ids is None or i["claim_id"] in claim_ids]


def _result(checks: dict) -> dict:
    return {"passed": all(checks.values()), "checks": checks}


def grade_s1(out, key):
    """Category drop: names the planted category and the state within it."""
    category = key["expected"]["top_category_by_revenue_change"]
    state = key["expected"]["top_state_within_category"]
    hits = [c for c in _claims(out["report_draft"]) if mentions(c["statement"], category)]
    located = [c for c in hits if mentions(c["statement"], state)]
    return _result({
        f"a claim names {category}": bool(hits),
        f"a claim locates it in {state}": bool(located),
        "located claim cites valid evidence": bool(located) and not _issues(
            out["report_draft"], {c["claim_id"] for c in located}),
    })


def grade_s2(out, key):
    """Delivery and reviews: a review drop in the state, reported as a pattern, never a cause."""
    state = key["expected"]["state_with_review_drop"]
    claims = [c for c in _claims(out["report_draft"]) if mentions(c["statement"], state)]
    review = [c for c in claims if c["question_id"] == "Q2"]
    pattern = [c for c in claims if c["claim_type"] == "observed_pattern"]
    causal = [i for i in _issues(out["report_draft"], {c["claim_id"] for c in claims})
              if i["issue"] == "causal_language"]
    return _result({
        f"a Q2 claim names {state}": bool(review),
        "the link is an observed_pattern": bool(pattern),
        "no causal wording": not causal,
    })


def grade_s3(out, key):
    """Broad drop with no driver: names the state and labels it unexplained."""
    state = key["expected"]["top_state_by_revenue_change"]
    claims = [c for c in _claims(out["report_draft"]) if mentions(c["statement"], state)]
    unexplained = [c for c in claims if c["claim_type"] == "unexplained"]
    false_pattern = [c for c in claims if c["claim_type"] == "observed_pattern"]
    return _result({
        f"a claim names {state}": bool(claims),
        "labeled unexplained": bool(unexplained),
        "unexplained claim flagged for human review": all(c["requires_human_review"] for c in unexplained)
                                                       and bool(unexplained),
        "no invented delivery pattern": not false_pattern,
    })


def grade_partial(out, key):
    """Incomplete week, missing data, or a failed tool: the report must say PARTIAL."""
    report = out["report_draft"]
    return _result({"report is PARTIAL": bool(report) and report["draft_status"] == "PARTIAL"})


def grade_s5(out, key):
    """Tool failure: PARTIAL, the failure listed, and no claim built on it."""
    report = out["report_draft"]
    listed = bool(report) and any("avg_review_score" in u for u in report["unavailable"])
    return _result({
        "report is PARTIAL": bool(report) and report["draft_status"] == "PARTIAL",
        "failure listed as unavailable": listed,
        "no claim cites the failed call": bool(report) and not any(
            i["issue"] == "cites_failed_call" for i in report["precheck_issues"]),
    })


GRADERS = {
    "S1_category_drop": grade_s1,
    "S2_delivery_and_reviews": grade_s2,
    "S3_unexplained_drop": grade_s3,
    "S4_incomplete_week": grade_partial,
    "S5_tool_failure": grade_s5,
    "S7_missing_rows": grade_partial,
}


def grade_run(out, key) -> dict:
    """Scenario checks + general checks that apply to every run."""
    if out["run"]["status"] != "ok":
        return {"passed": False, "checks": {"run completed": False},
                "error": out["run"].get("error"), "process": {}}
    result = GRADERS[key["id"]](out, key)
    honesty = [i for i in out["report_draft"]["precheck_issues"] if i["issue"] in HONESTY_ISSUES]
    result["checks"]["no honesty issues in the report"] = not honesty
    result["passed"] = all(result["checks"].values())
    result["process"] = {   # reported, not graded
        "tool_calls": out["run"]["tool_calls"],
        "within_budget": out["run"]["tool_calls"] <= out["run"]["budget"],
        "blocked_calls": len(out["run"]["blocked_calls"]),
        "tools_used": sorted({c["tool"] for c in out.get("calls", [])}),
    }
    return result
