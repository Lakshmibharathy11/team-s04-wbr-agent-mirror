from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from .wbr_contract import ContractError, guard_noncausal


VALID_TOOL_STATUSES = {"ok", "partial", "error"}
VALID_CLAIM_TYPES = {"fact", "observed_pattern", "unexplained"}
VALID_OUTCOMES = {"keep", "downgrade", "flag", "remove"}


@dataclass(frozen=True)
class RunResult:
    status: str
    tool_results: tuple[dict[str, Any], ...]
    claims: tuple[dict[str, Any], ...]
    removed_claims: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    renderer_input: dict[str, Any]
    rendered: Any = None


def _require_keys(payload: Mapping[str, Any], keys: Iterable[str], label: str) -> None:
    missing = [key for key in keys if key not in payload]
    if missing:
        raise ContractError(f"{label} missing required keys: {', '.join(missing)}")


def validate_tool_envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ContractError("tool result must be a mapping")
    _require_keys(
        payload,
        (
            "tool_name",
            "request_id",
            "status",
            "generated_at",
            "parameters",
            "data",
            "evidence_refs",
            "warnings",
            "error",
        ),
        "tool envelope",
    )

    result = dict(payload)
    if result["status"] not in VALID_TOOL_STATUSES:
        raise ContractError("tool status must be ok, partial, or error")
    if not isinstance(result["tool_name"], str) or not result["tool_name"].strip():
        raise ContractError("tool_name must be a non-empty string")
    if not isinstance(result["request_id"], str) or not result["request_id"].strip():
        raise ContractError("request_id must be a non-empty string")
    if not isinstance(result["parameters"], Mapping):
        raise ContractError("parameters must be a mapping")
    if not isinstance(result["evidence_refs"], list) or any(
        not isinstance(ref, str) or not ref.strip() for ref in result["evidence_refs"]
    ):
        raise ContractError("evidence_refs must be a list of non-empty strings")
    if not isinstance(result["warnings"], list) or any(not isinstance(w, str) for w in result["warnings"]):
        raise ContractError("warnings must be a list of strings")
    if result["status"] == "ok" and not result["evidence_refs"]:
        raise ContractError("ok tool results require supporting evidence_refs")
    if result["status"] == "error" and not result["error"]:
        raise ContractError("error tool results require an error payload")
    return result


def validate_claim(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ContractError("analyst claim must be a mapping")
    _require_keys(
        payload,
        (
            "claim_id",
            "question_id",
            "statement",
            "claim_type",
            "confidence",
            "supporting_evidence",
            "causal_language_allowed",
            "requires_human_review",
        ),
        "analyst claim",
    )
    claim = dict(payload)
    if not isinstance(claim["claim_id"], str) or not claim["claim_id"].strip():
        raise ContractError("claim_id must be a non-empty string")
    if not isinstance(claim["question_id"], str) or not claim["question_id"].strip():
        raise ContractError("question_id must be a non-empty string")
    if claim["claim_type"] not in VALID_CLAIM_TYPES:
        raise ContractError("invalid claim_type")
    if not isinstance(claim["statement"], str) or not claim["statement"].strip():
        raise ContractError("claim statement must be non-empty")
    if not isinstance(claim["supporting_evidence"], list) or any(
        not isinstance(ref, str) or not ref.strip() for ref in claim["supporting_evidence"]
    ):
        raise ContractError("supporting_evidence must be a list of non-empty strings")
    if not isinstance(claim["causal_language_allowed"], bool):
        raise ContractError("causal_language_allowed must be boolean")
    if not isinstance(claim["requires_human_review"], bool):
        raise ContractError("requires_human_review must be boolean")
    return claim


def retry_tool_once(
    tool_name: str,
    call: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    parameters: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    last_error = "unknown tool failure"
    last_valid_error_envelope: Optional[dict[str, Any]] = None

    for attempt in (1, 2):
        try:
            result = validate_tool_envelope(call(parameters))
            if result["tool_name"] != tool_name:
                raise ContractError(
                    f"tool envelope name mismatch: expected {tool_name}, got {result['tool_name']}"
                )
            if result["status"] != "error":
                return result, warnings
            last_valid_error_envelope = result
            last_error = str(result.get("error") or "tool returned error")
        except Exception as exc:
            last_error = str(exc)

        if attempt == 1:
            warnings.append(f"{tool_name}: first attempt failed; retried once")

    if last_valid_error_envelope is not None:
        failure = dict(last_valid_error_envelope)
        failure["warnings"] = list(failure.get("warnings", [])) + ["retry exhausted"]
        return failure, warnings

    failure = {
        "tool_name": tool_name,
        "request_id": f"{tool_name}-retry-exhausted",
        "status": "error",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parameters": dict(parameters),
        "data": None,
        "evidence_refs": [],
        "warnings": ["retry exhausted"],
        "error": last_error,
    }
    return failure, warnings


def default_verify_claim(
    claim: Mapping[str, Any],
    evidence_by_ref: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    """Fail-closed fallback verifier.

    Evidence-reference membership alone is not semantic proof that a claim matches
    the underlying tool payload. Without a trusted verifier callback that inspects
    referenced payloads, this fallback may remove or flag claims but never keep
    them solely because references exist.
    """

    claim = validate_claim(claim)
    supporting = set(claim["supporting_evidence"])
    available_evidence = set(evidence_by_ref)

    if not supporting or not supporting.issubset(available_evidence):
        return {
            "claim_id": claim["claim_id"],
            "outcome": "remove",
            "reason": "missing or unavailable evidence",
        }

    # Analyst-provided causal_language_allowed is not an authorization boundary.
    # The fallback verifier always enforces the non-causal guard.
    try:
        guard_noncausal(claim["statement"])
    except ContractError:
        return {
            "claim_id": claim["claim_id"],
            "outcome": "flag",
            "reason": "unsupported causal language",
        }

    if claim["requires_human_review"]:
        return {
            "claim_id": claim["claim_id"],
            "outcome": "flag",
            "reason": "human review required",
        }

    return {
        "claim_id": claim["claim_id"],
        "outcome": "flag",
        "reason": "semantic verification unavailable",
    }


def apply_verifier_outcomes(
    claims: Sequence[dict[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    outcome_by_id: dict[str, Mapping[str, Any]] = {}
    for outcome in outcomes:
        if outcome.get("outcome") not in VALID_OUTCOMES:
            raise ContractError("invalid verifier outcome")
        outcome_by_id[str(outcome.get("claim_id"))] = outcome

    accepted: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for claim in claims:
        outcome = outcome_by_id.get(str(claim["claim_id"]))
        if outcome is None:
            outcome = {
                "claim_id": claim["claim_id"],
                "outcome": "remove",
                "reason": "missing verifier outcome",
            }
        enriched = dict(claim)
        enriched["verifier_outcome"] = outcome["outcome"]
        enriched["verifier_reason"] = outcome.get("reason")
        if outcome["outcome"] == "remove":
            removed.append(enriched)
        else:
            accepted.append(enriched)
    return accepted, removed


def run_wbr(
    tool_calls: Sequence[
        tuple[str, Callable[[Mapping[str, Any]], Mapping[str, Any]], Mapping[str, Any]]
    ],
    analyst: Callable[[Sequence[Mapping[str, Any]]], Sequence[Mapping[str, Any]]],
    *,
    verifier: Optional[
        Callable[
            [Mapping[str, Any], Mapping[str, Mapping[str, Any]]],
            Mapping[str, Any],
        ]
    ] = None,
    renderer: Optional[Callable[[Mapping[str, Any]], Any]] = None,
    expected_tool_names: Sequence[str],
    label: str = "SYNTHETIC",
) -> RunResult:
    """Mockable orchestration boundary for tools -> analyst -> verifier -> renderer."""

    tool_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    by_name: dict[str, dict[str, Any]] = {}
    duplicate_tool_names: set[str] = set()

    for tool_name, call, parameters in tool_calls:
        if tool_name in by_name:
            duplicate_tool_names.add(tool_name)
        result, retry_warnings = retry_tool_once(tool_name, call, parameters)
        tool_results.append(result)
        by_name[tool_name] = result
        warnings.extend(retry_warnings)
        warnings.extend(result.get("warnings", []))

    missing_tools = [name for name in expected_tool_names if name not in by_name]
    unexpected_tools = [name for name in by_name if name not in expected_tool_names]
    if missing_tools:
        warnings.append("missing expected tools: " + ", ".join(missing_tools))
    if unexpected_tools:
        warnings.append("unexpected configured tools: " + ", ".join(unexpected_tools))
    if duplicate_tool_names:
        warnings.append("duplicate configured tools: " + ", ".join(sorted(duplicate_tool_names)))

    complete_tools = (
        not missing_tools
        and not unexpected_tools
        and not duplicate_tool_names
        and len(expected_tool_names) == len(set(expected_tool_names))
        and all(
            by_name[name]["status"] == "ok" and by_name[name]["evidence_refs"]
            for name in expected_tool_names
        )
    )

    return verify_and_render(
        tool_results, analyst, warnings=warnings, complete_tools=complete_tools,
        verifier=verifier, renderer=renderer, label=label,
    )


def verify_and_render(
    tool_results, analyst, *, warnings, complete_tools,
    verifier=None, renderer=None, label="SYNTHETIC", evidence_aliases=None,
) -> RunResult:
    """Shared verification boundary; never invokes or retries data tools."""
    warnings = list(warnings)
    evidence_by_ref: dict[str, Mapping[str, Any]] = {}
    duplicate_evidence_refs: set[str] = set()
    for result in tool_results:
        if result["status"] not in {"ok", "partial"}:
            continue
        for ref in result["evidence_refs"]:
            if ref in evidence_by_ref:
                duplicate_evidence_refs.add(ref)
                warnings.append(f"duplicate evidence reference: {ref}")
                continue
            evidence_by_ref[ref] = result

    if duplicate_evidence_refs:
        complete_tools = False

    for alias, captured in (evidence_aliases or {}).items():
        if captured["status"] not in {"ok", "partial"}:
            continue
        if alias in evidence_by_ref:
            raise ContractError("ambiguous evidence alias")
        evidence_by_ref[alias] = captured

    valid_claims: list[dict[str, Any]] = []
    rejected_claims = 0
    try:
        raw_claims = analyst(tuple(tool_results))
        if not isinstance(raw_claims, Sequence) or isinstance(raw_claims, (str, bytes)):
            raise ContractError("analyst response must be a sequence of claims")
        seen_claim_ids: set[str] = set()
        for index, raw_claim in enumerate(raw_claims):
            try:
                claim = validate_claim(raw_claim)
                claim_id = claim["claim_id"]
                if claim_id in seen_claim_ids:
                    raise ContractError(f"duplicate claim_id: {claim_id}")
                seen_claim_ids.add(claim_id)
                valid_claims.append(claim)
            except Exception as exc:
                rejected_claims += 1
                warnings.append(f"analyst claim {index} rejected as malformed: {exc}")
    except Exception as exc:
        warnings.append(f"analyst response unavailable or malformed: {exc}")
        valid_claims = []
        rejected_claims += 1

    verifier_fn = verifier or default_verify_claim
    outcomes: list[Mapping[str, Any]] = []
    for claim in valid_claims:
        try:
            supporting = set(claim["supporting_evidence"])
            evidence_available = bool(supporting) and supporting.issubset(set(evidence_by_ref))
            outcome = dict(verifier_fn(claim, evidence_by_ref))
            if outcome.get("claim_id") != claim["claim_id"]:
                raise ContractError("verifier claim_id mismatch")
            if outcome.get("outcome") not in VALID_OUTCOMES:
                raise ContractError("invalid verifier outcome")
            if outcome.get("outcome") == "keep" and not evidence_available:
                outcome = {
                    "claim_id": claim["claim_id"],
                    "outcome": "remove",
                    "reason": "missing or unavailable evidence",
                }
            outcomes.append(outcome)
        except Exception as exc:
            warnings.append(f"verifier failed for {claim['claim_id']}: {exc}")
            outcomes.append(
                {
                    "claim_id": claim["claim_id"],
                    "outcome": "remove",
                    "reason": "verifier failure",
                }
            )

    accepted_claims, removed_claims = apply_verifier_outcomes(valid_claims, outcomes)
    all_claims_supported = (
        rejected_claims == 0
        and bool(valid_claims)
        and all(claim["verifier_outcome"] == "keep" for claim in accepted_claims)
        and not removed_claims
    )

    status = "complete" if complete_tools and all_claims_supported else "partial"

    renderer_input = {
        "label": label,
        "status": status,
        "tool_results": tool_results,
        "claims": accepted_claims,
        "removed_claims": removed_claims,
        "warnings": warnings,
    }
    rendered = renderer(renderer_input) if renderer is not None else None

    return RunResult(
        status=status,
        tool_results=tuple(tool_results),
        claims=tuple(accepted_claims),
        removed_claims=tuple(removed_claims),
        warnings=tuple(warnings),
        renderer_input=renderer_input,
        rendered=rendered,
    )

