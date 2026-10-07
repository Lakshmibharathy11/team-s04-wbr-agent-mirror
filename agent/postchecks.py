"""Deterministic checks and contract fields added by code after the model writes the draft.

The model does not rate its own confidence or decide causal permissions: code does,
from the evidence. Problems are listed in precheck_issues for the verifier; claims are
not silently changed or removed here (removal is the verifier's decision).
"""

import re

CAUSAL_PATTERN = re.compile(
    r"\b(because|caused|causes|due to|led to|leads to|drove|driven by|as a result of|resulted in)\b",
    re.IGNORECASE)
WEAK_DATA = {"SMALL_SAMPLE", "INSUFFICIENT_HISTORY", "INVALID_DATA", "NO_DATA", "INCOMPLETE_WEEK",
             "ZERO_DENOMINATOR"}


def _max_abs_z(envelope: dict) -> float:
    data = envelope.get("data") or {}
    zs = []
    if "z_score" in data:
        zs.append(data["z_score"])
    for g in data.get("groups", [])[:1]:          # the top-ranked group
        zs.append(g.get("z_score"))
    for m in data.get("metrics", {}).values():
        zs.append(m.get("z_score"))
    zs = [abs(z) for z in zs if z is not None]
    return max(zs) if zs else 0.0


def _weak(env: dict) -> bool:
    """True if a cited result has a data-quality problem that should lower confidence.
    For a breakdown, only the top-ranked group's own warnings count (problems in small,
    unrelated groups do not weaken a claim about the top group)."""
    if env["status"] == "error":
        return True
    codes = {w["code"] for w in env.get("warnings", [])}
    if env["tool_name"] == "get_breakdown":
        groups = (env.get("data") or {}).get("groups", [])
        top = set(groups[0].get("warnings", [])) if groups else {"NO_DATA"}
        return bool((top & WEAK_DATA) or "INCOMPLETE_WEEK" in codes)
    return env["status"] != "ok" or bool(codes & WEAK_DATA)


def _confidence(cited: list[dict]) -> str:
    """Preliminary rule (to confirm with the team):
    low    - any cited result has a data-quality problem (see _weak)
    high   - the strongest cited signal has |z| >= 3
    medium - otherwise
    """
    if not cited or any(_weak(e) for e in cited):
        return "low"
    return "high" if max(_max_abs_z(e) for e in cited) >= 3 else "medium"


def finalize(draft, ctx) -> dict:
    """Turn the model's draft into the V1 report draft, plus pre-check notes."""
    claims, issues = [], []
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
        causal = CAUSAL_PATTERN.findall(c.statement)
        if causal:
            issues.append({"claim_id": claim_id, "issue": "causal_language",
                           "words": sorted({w.lower() for w in causal})})

        confidence = _confidence([e for e in cited if e["status"] != "error"]) if valid else "low"
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
    for call in ctx.calls:
        if call["status"] == "error":
            note = f"{call['tool']} {call['args']} failed"
            if note not in unavailable:
                unavailable.append(note)

    incomplete = any("INCOMPLETE_WEEK" in c["warnings"] for c in ctx.calls)
    draft_status = "PARTIAL" if (unavailable or incomplete) else "COMPLETE"
    return {"claims": claims, "actionable_questions": list(draft.actionable_questions),
            "unavailable": unavailable, "draft_status": draft_status, "precheck_issues": issues}
