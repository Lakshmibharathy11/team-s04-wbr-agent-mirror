"""Deterministic checks and contract fields added by code after the model writes the draft.

The model does not rate its own confidence or decide causal permissions: code does,
from the evidence. Problems are listed in precheck_issues for the verifier; claims are
not silently changed or removed here (removal is the verifier's decision).
"""

import re

from datetime import date, timedelta

from agent.evaluation import NAMES
from tools import config
from src.period_guard import period_problem

CAUSAL_PATTERN = re.compile(
    r"\b(because|caused|causes|causing|due to|led to|leads to|lead to|leading to|drove|driven by|as a result of|resulted in|results in|resulting in|resulted from|results from|resulting from|attributed to|attributable to|stemming from|contribute|contributes|contributed|contributing|contributor|contributors|may have|might have|could have|likely|impact|impacts|impacted|affect|affects|affected|explains|explained by|owing to|thanks to|triggered|triggers|triggering|spurred|fueled|fuelled|prompted|explain)\b",
    re.IGNORECASE)
WEAK_DATA = {"SMALL_SAMPLE", "INSUFFICIENT_HISTORY", "INVALID_DATA", "NO_DATA", "INCOMPLETE_WEEK",
             "ZERO_DENOMINATOR"}


# Groups never overlap: "Other" is everything not listed separately, so one group can
# never include, contain or be part of another group of the same dimension.
CONTAINMENT = re.compile(
    r"\b(includ\w*|contain\w*|compris\w*|consist\w* of|part of|subset of|belong\w* to|"
    r"made up of|is within|are within|covered by|covers)\b", re.IGNORECASE)


def _group_spans(statement: str, groups) -> list[tuple[int, str]]:
    found = []
    for g in groups:
        if g == "Other":
            pats = [r"(?<![A-Za-z0-9])Other(?![A-Za-z0-9])"]
        else:
            pats = []
            for alias in NAMES.get(g, [g]):
                if alias.isupper() and len(alias) == 2:
                    pats.append(rf"(?<![A-Za-z0-9]){alias}(?![A-Za-z0-9])")
                else:
                    words = re.split(r"[_\s&]+", alias.lower())
                    pats.append("(?i)(?<![a-z0-9])" + r"[\s_&-]+".join(map(re.escape, words)) + "(?![a-z0-9])")
        for pat in pats:
            found += [(m.start(), m.end(), g) for m in re.finditer(pat, statement)]
    return sorted(found)


def group_relationship(statement: str) -> list[str]:
    """Pairs of same-dimension groups the statement links with a containment word,
    e.g. "the 'Other' state, which includes the 'SP' state" -> ["Other includes SP"]."""
    out = []
    for groups in (config.STATE_GROUPS, config.CATEGORY_GROUPS):
        spans = _group_spans(statement, groups)
        for i, (_, a_end, a) in enumerate(spans):
            for b_start, _, b in spans[i + 1:]:
                gap = statement[a_end:b_start]
                if b != a and len(gap) <= 60 and CONTAINMENT.search(gap):
                    out.append(f"{a} {CONTAINMENT.search(gap).group(0)} {b}")
    return out


def wrong_week(env: dict, week: str) -> bool:
    """True if a result is not for the report week (its own previous week and baseline
    are part of that same result, so they are valid)."""
    return period_problem(env, week) is not None


def _mentions(statement: str, group: str) -> bool:
    """Same name forms the graders accept (agent/evaluation.py), but whole words only:
    state codes are case-sensitive (SP, RJ) or written out (Rio de Janeiro); category
    names may use spaces, underscores or & (health_beauty, health beauty, Health & Beauty)."""
    if group == "Other":   # capitalized only, so ordinary prose ("other states") is not a group
        return bool(re.search(r"(?<![A-Za-z0-9])Other(?![A-Za-z0-9])", statement))
    for alias in NAMES.get(group, [group]):
        if alias.isupper() and len(alias) == 2:
            if re.search(rf"(?<![A-Za-z0-9]){alias}(?![A-Za-z0-9])", statement):
                return True
        else:
            words = re.split(r"[_\s&]+", alias.lower())
            pattern = r"[\s_&-]+".join(map(re.escape, words))
            if re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", statement.lower()):
                return True
    return False


def _named_groups(env: dict, statement: str) -> list[dict]:
    """The breakdown groups the claim's statement names.
    Why: the agent picks groups by absolute_change or share, not by z-score rank, so
    confidence must come from the group the claim is about, not from groups[0]."""
    groups = (env.get("data") or {}).get("groups", [])
    return [g for g in groups if g.get("group") and _mentions(statement, g["group"])]


def _max_abs_z(env: dict, statement: str) -> float:
    data = env.get("data") or {}
    if env.get("tool_name") == "get_breakdown":
        # Weakest named group (conservative: a claim is only as strong as its weakest group)
        zs = [abs(g["z_score"]) if g.get("z_score") is not None else 0.0
              for g in _named_groups(env, statement)]
        return min(zs) if zs else 0.0
    zs = []
    if "z_score" in data:
        zs.append(data["z_score"])
    for m in data.get("metrics", {}).values():
        zs.append(m.get("z_score"))
    zs = [abs(z) for z in zs if z is not None]
    return max(zs) if zs else 0.0


def _weak(env: dict, statement: str) -> bool:
    """True if a cited result has a data-quality problem that should lower confidence.
    For a breakdown, the warnings of the groups the claim names count (problems in
    unrelated groups do not weaken it). A cited breakdown whose groups the claim does
    not name counts as weak: code cannot tell which group the claim relies on."""
    if env["status"] == "error":
        return True
    codes = {w["code"] for w in env.get("warnings", [])}
    if env["tool_name"] == "get_breakdown":
        named = _named_groups(env, statement)
        if not named:
            return True
        flags = set().union(*(set(g.get("warnings", [])) for g in named))
        return bool((flags & WEAK_DATA) or "INCOMPLETE_WEEK" in codes)
    return env["status"] != "ok" or bool(codes & WEAK_DATA)


def _confidence(cited: list[dict], statement: str) -> str:
    """Preliminary rule (to confirm with the team):
    low    - any cited result has a data-quality problem (see _weak)
    high   - the strongest cited signal has |z| >= 3 (for a breakdown: the named groups)
    medium - otherwise
    """
    if not cited or any(_weak(e, statement) for e in cited):
        return "low"
    return "high" if max(_max_abs_z(e, statement) for e in cited) >= 3 else "medium"


def _failure_note(call: dict, ctx) -> str:
    """A readable line for the report, e.g.
    'get_weekly_metric avg_review_score (state RJ) failed: timeout'."""
    args = call["args"]
    parts = [call["tool"]] + [str(args[k]) for k in ("metric", "dimension") if args.get(k)]
    filters = ", ".join(f"{k} {args[k]}" for k in ("state", "category") if args.get(k))
    if filters:
        parts.append(f"({filters})")
    error = (ctx.evidence_log.get(call["request_id"]) or {}).get("error") or {}
    return " ".join(parts) + " failed" + (f": {error['type']}" if error.get("type") else "")


def finalize(draft, ctx) -> dict:
    """Turn the model's draft into the V1 report draft, plus pre-check notes."""
    claims, issues = [], []
    period_issues = {rid: period_problem(env, ctx.week)
                     for rid, env in ctx.evidence_log.items()} if hasattr(ctx, "week") else {}
    period_issues = {rid: issue for rid, issue in period_issues.items() if issue}
    for i, c in enumerate(draft.claims, start=1):
        claim_id = f"c{i}"
        valid = [rid for rid in c.supporting_evidence if rid in ctx.evidence_log]
        unknown = [rid for rid in c.supporting_evidence if rid not in ctx.evidence_log]
        cited = [ctx.evidence_log[rid] for rid in valid]
        failed = [rid for rid in valid if ctx.evidence_log[rid]["status"] == "error"]

        if unknown:
            issues.append({"claim_id": claim_id, "issue": "unknown_evidence", "request_ids": unknown})
        if not valid:
            issues.append({"claim_id": claim_id, "issue": "no_valid_evidence"})
        if failed:
            issues.append({"claim_id": claim_id, "issue": "cites_failed_call", "request_ids": failed})
        week = getattr(ctx, "week", None)
        off_week = [rid for rid in valid if week and wrong_week(ctx.evidence_log[rid], week)]
        if off_week:
            issues.append({"claim_id": claim_id, "issue": "evidence_wrong_week",
                           "request_ids": off_week, "report_week": week})
            for issue in sorted({period_issues[rid] for rid in off_week}):
                issues.append({"claim_id": claim_id, "issue": issue,
                               "request_ids": [rid for rid in off_week if period_issues[rid] == issue]})
        links = group_relationship(c.statement)
        if links:
            issues.append({"claim_id": claim_id, "issue": "group_relationship", "links": links})
        causal = CAUSAL_PATTERN.findall(c.statement)
        if causal:
            issues.append({"claim_id": claim_id, "issue": "causal_language",
                           "words": sorted({w.lower() for w in causal})})

        confidence = (_confidence([e for e in cited if e["status"] != "error"], c.statement)
                      if valid else "low")
        if off_week:
            confidence = "low"
        claims.append({
            "claim_id": claim_id,
            "question_id": c.question_id,
            "statement": c.statement,
            "claim_type": c.claim_type,
            "confidence": confidence,
            "supporting_evidence": c.supporting_evidence,
            "causal_language_allowed": False,
            "requires_human_review": (c.claim_type != "fact" or confidence == "low"
                                      or any(x["claim_id"] == claim_id for x in issues)),
        })

    # Anything that failed is unavailable, whether or not the model listed it
    unavailable = list(draft.unavailable)
    for rid, issue in period_issues.items():
        unavailable.append(f"{rid}: {issue}; evidence cannot support run week {ctx.week}")
    for call in ctx.calls:
        if call["status"] == "error":   # every failed call stays visible, retried or not
            note = _failure_note(call, ctx)
            if note not in unavailable:
                unavailable.append(note)

    # PARTIAL when anything failed, the week is incomplete, or a result had missing/invalid data
    data_gap = any(c["status"] == "partial" and {"NO_DATA", "INVALID_DATA"} & set(c["warnings"])
                   for c in ctx.calls)
    incomplete = any("INCOMPLETE_WEEK" in c["warnings"] for c in ctx.calls)
    draft_status = "PARTIAL" if (unavailable or incomplete or data_gap) else "COMPLETE"
    return {"claims": claims, "actionable_questions": list(draft.actionable_questions),
            "unavailable": unavailable, "draft_status": draft_status, "precheck_issues": issues}
