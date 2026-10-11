import json
import unittest
from pathlib import Path

from src.wbr_runner import run_wbr


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "renderer_input_complete.json"

EXPECTED_TOOLS = (
    "weekly_revenue",
    "weekly_orders",
    "weekly_review_score",
    "category_breakdown",
    "state_breakdown",
)


class RendererBoundaryTests(unittest.TestCase):
    def test_complete_fixture_has_stable_renderer_shape(self):
        payload = json.loads(FIXTURE.read_text())

        self.assertEqual(payload["label"], "SYNTHETIC")
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(
            set(payload),
            {"label", "status", "tool_results", "claims", "removed_claims", "warnings"},
        )
        self.assertEqual(
            [result["tool_name"] for result in payload["tool_results"]],
            list(EXPECTED_TOOLS),
        )
        self.assertEqual(payload["claims"][0]["verifier_outcome"], "keep")
        self.assertEqual(payload["removed_claims"], [])

    def test_runner_preserves_opaque_tool_data_for_renderer(self):
        expected_data = {
            "weekly_revenue": {"shape": {"owned_by": "Lakshmi"}, "rows": [1, 2, 3]},
            "weekly_orders": {"anything": ["opaque", "to", "runner"]},
            "weekly_review_score": {"coverage": "not frozen here"},
            "category_breakdown": {"categories": [{"name": "synthetic"}]},
            "state_breakdown": {"states": [{"state": "CA"}]},
        }

        def envelope(name):
            return {
                "tool_name": name,
                "request_id": f"req-{name}",
                "status": "ok",
                "generated_at": "2026-10-05T00:00:00+00:00",
                "parameters": {"week": "synthetic"},
                "data": expected_data[name],
                "evidence_refs": [f"evidence:{name}"],
                "warnings": [],
                "error": None,
            }

        calls = [
            (name, lambda _params, payload=envelope(name): payload, {})
            for name in EXPECTED_TOOLS
        ]

        def analyst(_tool_results):
            return [
                {
                    "claim_id": "c1",
                    "question_id": "q1",
                    "statement": "Synthetic revenue changed.",
                    "claim_type": "fact",
                    "confidence": None,
                    "supporting_evidence": ["evidence:weekly_revenue"],
                    "causal_language_allowed": False,
                    "requires_human_review": False,
                }
            ]

        captured = {}

        def renderer(payload):
            captured.update(payload)
            return payload

        def verifier(claim, evidence_by_ref):
            supporting = set(claim["supporting_evidence"])
            return {
                "claim_id": claim["claim_id"],
                "outcome": "keep" if supporting.issubset(set(evidence_by_ref)) else "remove",
                "reason": "trusted renderer-boundary verifier",
            }

        result = run_wbr(
            calls,
            analyst,
            verifier=verifier,
            renderer=renderer,
            expected_tool_names=EXPECTED_TOOLS,
        )

        self.assertEqual(result.status, "partial")
        by_name = {item["tool_name"]: item["data"] for item in captured["tool_results"]}
        self.assertEqual(by_name, expected_data)


if __name__ == "__main__":
    unittest.main()

