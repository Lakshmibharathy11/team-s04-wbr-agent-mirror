"""Synthetic contract fixtures modeled on Lakshmi's checked builders, not AWS evidence."""
import unittest
from copy import deepcopy

from src.analyst_adapter import run_analyst_week
from src.wbr_contract import ContractError
from test_wbr_runner import good_claims


TOOLS = ["get_weekly_metric", "get_breakdown", "get_delivery_signals"]


def capture(name, rid):
    return {"tool_name": name, "request_id": rid, "status": "ok",
            "generated_at": "2026-10-07T00:00:00+00:00",
            "parameters": {"week": "2018-06-04", "compare_week": "2018-05-28"}, "data": {"value": 42},
            "evidence_refs": [{"query_id": "weekly_metric", "backend": "duckdb",
                               "table": "weekly_metrics", "row_count": 8,
                               "executed_at": "2026-10-07T00:00:00+00:00"}],
            "warnings": [], "error": None}


class LakshmiHandoffTests(unittest.TestCase):
    def response(self):
        log = {"req-" + name: capture(name, "req-" + name) for name in TOOLS}
        claims = good_claims([])
        claims[0]["supporting_evidence"] = ["req-get_weekly_metric"]
        return {"run": {"status": "ok"}, "evidence_log": log,
                "report_draft": {"week": "2018-06-04", "compare_week": "2018-05-28", "claims": claims,
                                 "draft_status": "COMPLETE", "unavailable": [],
                                 "precheck_issues": [], "actionable_questions": ["What changed?"]},
                "transcript": [{"role": "tool", "content": "view"}], "calls": []}

    @staticmethod
    def verifier(claim, evidence):
        # Synthetic assertion only; not a production semantic verifier.
        return {"claim_id": claim["claim_id"], "outcome": "keep"}

    def run_response(self, response, verifier=None, renderer=None):
        return run_analyst_week("2018-06-04", lambda week: response,
                                expected_tool_names=TOOLS,
                                verifier=verifier or self.verifier, renderer=renderer)

    def test_nested_claims_and_request_id_resolve_exact_raw_capture(self):
        response = self.response()
        original = deepcopy(response)
        def verifier(claim, evidence):
            self.assertEqual(evidence["req-get_weekly_metric"], original["evidence_log"]["req-get_weekly_metric"])
            self.assertEqual(claim["supporting_evidence"], ["req-get_weekly_metric"])
            return self.verifier(claim, evidence)
        result = self.run_response(response, verifier=verifier)
        self.assertEqual(result.status, "partial")
        self.assertEqual(response, original)
        self.assertEqual(result.tool_results[0]["source_envelope"], original["evidence_log"]["req-get_weekly_metric"])

    def test_structured_warning_is_preserved_raw_and_rendered(self):
        response = self.response()
        warning = {"code": "SMALL_SAMPLE", "message": "Only 12 orders", "details": {"orders": 12}}
        response["evidence_log"]["req-get_weekly_metric"]["warnings"] = [warning]
        def verifier(claim, evidence):
            self.assertEqual(evidence["req-get_weekly_metric"]["warnings"], [warning])
            return self.verifier(claim, evidence)
        result = self.run_response(response, verifier=verifier)
        self.assertTrue(any("SMALL_SAMPLE" in w for w in result.warnings))

    def test_missing_request_id_cannot_be_kept(self):
        response = self.response()
        response["report_draft"]["claims"][0]["supporting_evidence"] = ["req-fake"]
        result = self.run_response(response)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.removed_claims[0]["verifier_outcome"], "remove")

    def test_failed_request_is_not_available_evidence(self):
        response = self.response()
        e = response["evidence_log"]["req-get_weekly_metric"]
        e.update(status="error", data=None, evidence_refs=[], error={"type": "timeout", "message": "timeout"})
        result = self.run_response(response)
        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.removed_claims), 1)

    def test_shared_query_metadata_does_not_collapse_distinct_calls(self):
        response = self.response()
        e = capture("get_weekly_metric", "req-another")
        response["evidence_log"]["req-another"] = e
        result = self.run_response(response)
        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.tool_results), 4)

    def test_draft_partial_unavailable_or_precheck_never_complete(self):
        for key, value in [("draft_status", "PARTIAL"), ("unavailable", ["missing"]),
                           ("precheck_issues", [{"issue": "unknown_evidence"}])]:
            response = self.response()
            response["report_draft"][key] = value
            with self.subTest(key=key):
                self.assertEqual(self.run_response(response).status, "partial")

    def test_run_failure_with_null_draft_preserves_logged_results(self):
        response = self.response()
        response["run"] = {"status": "error", "error": "model failure"}
        response["report_draft"] = None
        result = self.run_response(response)
        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.tool_results), 3)
        self.assertFalse(result.claims)

    def test_renderer_receives_separately_labeled_unverified_metadata(self):
        response = self.response()
        seen = []
        result = self.run_response(response, renderer=lambda p: seen.append(p))
        self.assertEqual(seen[0]["analyst_draft"], response["report_draft"])
        self.assertEqual(seen[0]["analyst_transcript"], response["transcript"])
        self.assertEqual(result.renderer_input["claims"][0]["verifier_outcome"], "keep")

    def test_malformed_structured_reference_rejected(self):
        for key, value in [("row_count", -1), ("row_count", True), ("query_id", "")]:
            response = self.response()
            response["evidence_log"]["req-get_weekly_metric"]["evidence_refs"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ContractError):
                self.run_response(response)

    def test_malformed_structured_warning_rejected(self):
        response = self.response()
        response["evidence_log"]["req-get_weekly_metric"]["warnings"] = [{"code": "NO_DATA"}]
        with self.assertRaises(ContractError):
            self.run_response(response)

    def test_nested_draft_requires_claim_list(self):
        response = self.response()
        del response["report_draft"]["claims"]
        with self.assertRaises(ContractError):
            self.run_response(response)

    def test_alias_collision_is_rejected(self):
        response = self.response()
        response["evidence_log"]["req-get_breakdown"]["evidence_refs"] = ["req-get_weekly_metric"]
        with self.assertRaises(ContractError):
            self.run_response(response)

    def test_no_trusted_verifier_remains_partial(self):
        response = self.response()
        result = run_analyst_week("2018-06-04", lambda week: response, expected_tool_names=TOOLS)
        self.assertEqual(result.status, "partial")


if __name__ == "__main__":
    unittest.main()
