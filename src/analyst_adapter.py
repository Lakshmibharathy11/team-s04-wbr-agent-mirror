from __future__ import annotations

from copy import deepcopy
import json
from datetime import date, timedelta
from typing import Mapping

from .wbr_contract import ContractError, guard_noncausal
from .wbr_runner import validate_tool_envelope, verify_and_render
from .numeric_verifier import make_captured_verifier
from .period_guard import period_problem


def normalize_captured_envelope(raw):
    """Bridge structured tool metadata without losing the original capture."""
    if not isinstance(raw, Mapping):
        raise ContractError("captured tool envelope must be a mapping")
    normalized = deepcopy(dict(raw))
    request_id = normalized.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ContractError("request_id must be a non-empty string")
    refs = normalized.get("evidence_refs")
    warnings = normalized.get("warnings")
    if not isinstance(refs, list) or not isinstance(warnings, list):
        raise ContractError("evidence_refs and warnings must be lists")
    converted = []
    for index, ref in enumerate(refs):
        if isinstance(ref, str):
            converted.append(ref)
        elif isinstance(ref, Mapping):
            for key in ("query_id", "backend", "table", "executed_at"):
                if not isinstance(ref.get(key), str) or not ref[key].strip():
                    raise ContractError("structured evidence lacks " + key)
            count = ref.get("row_count")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ContractError("structured evidence requires non-negative row_count")
            # A capture-local lookup key, not an assertion of query authenticity.
            converted.append(request_id + ":source:" + str(index))
        else:
            raise ContractError("invalid evidence reference")
    rendered_warnings = []
    for warning in warnings:
        if isinstance(warning, str):
            rendered_warnings.append(warning)
        elif isinstance(warning, Mapping):
            if any(not isinstance(warning.get(key), str) or not warning[key].strip()
                   for key in ("code", "message")):
                raise ContractError("structured warning requires code and message")
            if not isinstance(warning.get("details", {}), Mapping):
                raise ContractError("warning details must be a mapping")
            rendered_warnings.append(json.dumps(dict(warning), sort_keys=True))
        else:
            raise ContractError("invalid warning")
    normalized["evidence_refs"] = converted
    normalized["warnings"] = rendered_warnings
    normalized["source_envelope"] = deepcopy(dict(raw))
    return validate_tool_envelope(normalized)


def run_analyst_week(
    week, run_analyst, *, expected_tool_names, verifier=None,
    renderer=None, label="SYNTHETIC",
):
    """Adapt trusted tool-wrapper captures from run_analyst(week) to RunResult.

    report_draft is a nested report or legacy list. evidence_log is a request_id-keyed
    mapping populated by tool wrappers, outside model-generated text.
    Repeated tool names are allowed; request IDs and evidence refs are unique.
    """
    try:
        response = run_analyst(week)
    except Exception:
        # Model/SDK initialization can fail before the analyst builds its envelope.
        # Never retry here, invent evidence, or expose exception text (credentials
        # and private request content can appear in provider exceptions).
        response = {
            "report_draft": None,
            "evidence_log": {},
            "run": {"status": "error", "error": "ANALYST_CALL_FAILED"},
        }
    if not isinstance(response, Mapping):
        raise ContractError("analyst result must be a mapping")
    if "report_draft" not in response or "evidence_log" not in response:
        raise ContractError("analyst result requires report_draft and evidence_log")
    response = deepcopy(response)
    draft = response["report_draft"]
    run = response.get("run", {})
    if not isinstance(run, Mapping):
        raise ContractError("run metadata must be a mapping")
    nested = isinstance(draft, Mapping)
    if nested:
        claims = draft.get("claims")
    elif draft is None and run.get("status") == "error":
        claims = []
    else:
        claims = draft
    if not isinstance(claims, list):
        raise ContractError("report_draft must be a list of V1 claims")
    log = response["evidence_log"]
    if not isinstance(log, Mapping):
        raise ContractError("evidence_log must be a request_id-keyed mapping")
    envelopes = []
    refs = set()
    for request_id, raw in log.items():
        envelope = normalize_captured_envelope(raw)
        if request_id != envelope["request_id"]:
            raise ContractError("evidence_log key must equal envelope request_id")
        for ref in envelope["evidence_refs"]:
            if ref in refs:
                raise ContractError("ambiguous duplicate evidence reference")
            refs.add(ref)
        envelopes.append(envelope)
    expected = tuple(expected_tool_names)
    observed = {item["tool_name"] for item in envelopes}
    warnings = [warning for item in envelopes for warning in item["warnings"]]
    wrong_periods = {item["request_id"]: period_problem(item["source_envelope"], week)
                     for item in envelopes}
    wrong_periods = {rid: issue for rid, issue in wrong_periods.items() if issue}
    warnings.extend(f"{rid}: {issue}" for rid, issue in wrong_periods.items())
    draft_period_ok = not nested or (
        draft.get("week") == week
        and draft.get("compare_week") ==
             (date.fromisoformat(week) - timedelta(days=7)).isoformat()
    )
    if not draft_period_ok:
        warnings.append("analyst draft period does not match run week")
    missing = set(expected) - observed
    unexpected = observed - set(expected)
    if missing:
        warnings.append("missing expected tools: " + ", ".join(sorted(missing)))
    if unexpected:
        warnings.append("unexpected tools: " + ", ".join(sorted(unexpected)))
    complete = (
        bool(expected) and len(expected) == len(set(expected))
        and not wrong_periods and draft_period_ok
        and not missing and not unexpected
        and all(item["status"] == "ok" for item in envelopes)
        and run.get("status", "ok") == "ok"
        and (not nested or (
            draft.get("draft_status") == "COMPLETE"
            and not draft.get("unavailable") and not draft.get("precheck_issues")
        ))
    )
    if run.get("status") == "error":
        warnings.append("analyst run failed: " + str(run.get("error", "unknown error")))
    aliases = {item["request_id"]: deepcopy(item["source_envelope"])
               for item in envelopes} if nested else {}

    def render_with_context(payload):
        # Draft questions/metadata remain explicitly unverified analyst output.
        payload["analyst_draft"] = deepcopy(draft)
        payload["analyst_run"] = deepcopy(run)
        payload["analyst_transcript"] = deepcopy(response.get("transcript", []))
        payload["analyst_calls"] = deepcopy(response.get("calls", []))
        return renderer(payload) if renderer is not None else None

    delegated_verifier = verifier or make_captured_verifier(week)

    def verify_period_first(claim, evidence):
        if not draft_period_ok or any(
            period_problem(evidence[ref].get("source_envelope", evidence[ref]), week)
            for ref in claim["supporting_evidence"] if ref in evidence
        ):
            return {"claim_id": claim["claim_id"], "outcome": "remove",
                    "reason": "evidence period does not match run week"}
        # These deterministic boundaries also apply to explicit verifier callbacks.
        from agent.postchecks import group_relationship
        if group_relationship(claim["statement"]):
            return {"claim_id": claim["claim_id"], "outcome": "flag",
                    "reason": "group containment requires review"}
        try:
            guard_noncausal(claim["statement"])
        except ContractError:
            return {"claim_id": claim["claim_id"], "outcome": "flag",
                    "reason": "unsupported causal language"}
        return delegated_verifier(claim, evidence)

    return verify_and_render(
        envelopes, lambda _envelopes: claims, warnings=warnings,
        complete_tools=complete, verifier=verify_period_first, renderer=render_with_context,
        label=label, evidence_aliases=aliases,
    )
