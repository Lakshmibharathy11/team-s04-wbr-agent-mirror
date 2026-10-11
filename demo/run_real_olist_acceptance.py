from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.wbr_runner import run_wbr


EXPECTED_STATUS_BY_LABEL = {
    "PLANTED_REAL_OLIST": "complete",
    "REAL_OLIST_INCOMPLETE": "partial",
}


def index_tool_results(tool_results: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Index fixture tool results without silently collapsing duplicates."""

    by_name: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(tool_results):
        if not isinstance(item, Mapping):
            raise ValueError(f"tool_results[{index}] must be a mapping")
        tool_name = item.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError(f"tool_results[{index}].tool_name must be a non-empty string")
        if tool_name in by_name:
            raise ValueError(f"duplicate tool result in fixture: {tool_name}")
        by_name[tool_name] = item
    return by_name


def make_answer_key_verifier(answer_key: Mapping[str, Mapping[str, Any]]):
    """Build a verifier that requires each claim to cite its trusted answer-key evidence."""

    def verifier(claim, evidence_by_ref):
        expected = answer_key.get(claim["claim_id"])
        if expected is None:
            return {
                "claim_id": claim["claim_id"],
                "outcome": "remove",
                "reason": "claim absent from trusted answer key",
            }

        required = set(expected.get("required_evidence_refs", []))
        cited = set(claim.get("supporting_evidence", []))
        available = set(evidence_by_ref)

        if not required:
            return {
                "claim_id": claim["claim_id"],
                "outcome": "remove",
                "reason": "answer key has no required evidence",
            }
        if not required.issubset(cited):
            return {
                "claim_id": claim["claim_id"],
                "outcome": "remove",
                "reason": "claim does not cite required answer-key evidence",
            }
        if not required.issubset(available):
            return {
                "claim_id": claim["claim_id"],
                "outcome": "remove",
                "reason": "answer-key evidence unavailable",
            }

        expected_statement = expected.get("expected_statement")
        if not isinstance(expected_statement, str) or not expected_statement.strip():
            return {
                "claim_id": claim["claim_id"],
                "outcome": "remove",
                "reason": "answer key has no exact expected statement",
            }
        if claim.get("statement") != expected_statement:
            return {
                "claim_id": claim["claim_id"],
                "outcome": "remove",
                "reason": "claim statement differs from trusted answer key",
            }
        if expected.get("outcome") not in {"keep", "downgrade", "flag", "remove"}:
            return {
                "claim_id": claim["claim_id"],
                "outcome": "remove",
                "reason": "answer key has invalid verifier outcome",
            }

        return {
            "claim_id": claim["claim_id"],
            "outcome": expected["outcome"],
            "reason": "trusted planted-scenario answer key",
        }

    return verifier


def require_expected_status(label: str, actual_status: str) -> None:
    expected_status = EXPECTED_STATUS_BY_LABEL[label]
    if actual_status != expected_status:
        raise ValueError(
            f"acceptance status mismatch for {label}: "
            f"expected {expected_status}, got {actual_status}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run deterministic acceptance against real-Olist tool envelopes and a trusted planted-scenario answer key."
    )
    parser.add_argument("--fixture", required=True, help="JSON fixture containing tool_results, claims, and answer_key")
    args = parser.parse_args()

    fixture = json.loads(Path(args.fixture).read_text())
    label = fixture.get("label")
    if label not in EXPECTED_STATUS_BY_LABEL:
        raise SystemExit("fixture label must be PLANTED_REAL_OLIST or REAL_OLIST_INCOMPLETE")

    expected_tools = tuple(fixture["expected_tool_names"])
    tool_results = fixture["tool_results"]
    claims = fixture["claims"]
    answer_key = fixture["answer_key"]

    try:
        by_name = index_tool_results(tool_results)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    calls = [
        (
            name,
            lambda _params, payload=by_name[name]: payload,
            dict(by_name[name].get("parameters", {})),
        )
        for name in expected_tools
        if name in by_name
    ]

    def analyst(_results):
        return claims

    result = run_wbr(
        calls,
        analyst,
        verifier=make_answer_key_verifier(answer_key),
        expected_tool_names=expected_tools,
        label=label,
    )

    output = {
        "fixture": str(Path(args.fixture)),
        "label": label,
        "status": result.status,
        "renderer_input": result.renderer_input,
    }
    print(json.dumps(output, indent=2, sort_keys=True))

    try:
        require_expected_status(label, result.status)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()


