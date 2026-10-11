"""Private proposed integration entry point for an already captured analyst run.

Uses the existing runner adapter. No analyst, model, Athena or other data call
is accepted by this API. Expected coverage comes from the engineering owner.
"""
from copy import deepcopy
from collections.abc import Mapping

from src.analyst_adapter import run_analyst_week
from src.wbr_contract import ContractError


def run_captured(run, *, week, expected_tool_names, verifier=None,
                 renderer=None, label='PRIVATE_CAPTURE_REPLAY'):
    if not isinstance(run, Mapping):
        raise ContractError('captured run must be a mapping')
    metadata = run.get('run')
    if not isinstance(metadata, Mapping) or metadata.get('status') not in ('ok', 'error'):
        raise ContractError('captured run requires explicit run status')
    draft = run.get('report_draft')
    if draft is not None and not isinstance(draft, Mapping):
        raise ContractError('captured run requires a nested report draft')
    if isinstance(draft, Mapping):
        for field in ('claims', 'draft_status', 'unavailable', 'precheck_issues'):
            if field not in draft:
                raise ContractError('captured report missing ' + field)
        if draft['draft_status'] not in ('COMPLETE', 'PARTIAL'):
            raise ContractError('unknown draft status')
        for field in ('claims', 'unavailable', 'precheck_issues'):
            if not isinstance(draft[field], list):
                raise ContractError('captured report ' + field + ' must be a list')
        if draft.get('week', week) != week:
            raise ContractError('captured report week does not match replay week')
    expected = tuple(expected_tool_names)
    if not expected or any(not isinstance(name, str) or not name.strip() for name in expected):
        raise ContractError('externally specified expected tool coverage is required')
    snapshot = deepcopy(dict(run))
    return run_analyst_week(
        week, lambda _week: deepcopy(snapshot), expected_tool_names=expected,
        verifier=verifier, renderer=renderer, label=label,
    )
