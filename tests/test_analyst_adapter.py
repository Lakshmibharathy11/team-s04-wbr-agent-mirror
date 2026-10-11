import unittest
from copy import deepcopy

from src.analyst_adapter import run_analyst_week
from src.wbr_contract import ContractError
from test_wbr_runner import envelope, good_claims


class AnalystAdapterTests(unittest.TestCase):
    def response(self):
        e = envelope("weekly_revenue", data={"value": 42})
        e["parameters"].update(week="2018-08-13", compare_week="2018-08-06")
        return {"report_draft": good_claims([]), "evidence_log": {e["request_id"]: e}}

    def run_response(self, response, **kwargs):
        return run_analyst_week("2018-08-13", lambda week: response,
                                expected_tool_names=["weekly_revenue"], **kwargs)

    def test_exact_evidence_reaches_verifier_and_renderer(self):
        response = self.response()
        before = deepcopy(response)
        seen = []
        def verifier(claim, evidence):
            self.assertEqual(evidence["athena:weekly_revenue"]["data"], {"value": 42})
            self.assertEqual(evidence["athena:weekly_revenue"]["request_id"], "req-weekly_revenue")
            return {"claim_id": claim["claim_id"], "outcome": "keep"}
        result = self.run_response(response, verifier=verifier, renderer=lambda p: seen.append(p))
        self.assertEqual(result.status, "partial")
        self.assertEqual(len(seen), 1)
        self.assertEqual(response, before)

    def test_no_semantic_verifier_stays_partial(self):
        self.assertEqual(self.run_response(self.response()).status, "partial")

    def test_repeated_tool_calls_preserved_without_replay(self):
        response = self.response()
        e = envelope("weekly_revenue", refs=["athena:prior"])
        e["request_id"] = "req-prior"
        response["evidence_log"]["req-prior"] = e
        calls = []
        result = run_analyst_week("2018-08-13", lambda week: (calls.append(week) or response),
                                 expected_tool_names=["weekly_revenue"])
        self.assertEqual(calls, ["2018-08-13"])
        self.assertEqual(len(result.tool_results), 2)

    def test_error_envelope_preserved_without_retry(self):
        response = self.response()
        e = envelope("weekly_revenue", status="error", error="Athena unavailable")
        response["evidence_log"] = {e["request_id"]: e}
        result = self.run_response(response)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.tool_results[0]["error"], "Athena unavailable")
        self.assertFalse(any("retried" in w for w in result.warnings))

    def test_key_mismatch_rejected(self):
        response = self.response()
        response["evidence_log"] = {"different": next(iter(response["evidence_log"].values()))}
        with self.assertRaises(ContractError):
            self.run_response(response)

    def test_duplicate_evidence_is_rejected(self):
        response = self.response()
        e = deepcopy(next(iter(response["evidence_log"].values())))
        e["request_id"] = "second"
        response["evidence_log"]["second"] = e
        with self.assertRaises(ContractError):
            self.run_response(response)

    def test_malformed_shapes_rejected(self):
        for response in (None, {}, {"report_draft": {}, "evidence_log": {}},
                         {"report_draft": [], "evidence_log": []}):
            with self.subTest(response=response), self.assertRaises(ContractError):
                self.run_response(response)

    def test_missing_reference_cannot_be_kept(self):
        response = self.response()
        response["report_draft"][0]["supporting_evidence"] = ["missing"]
        result = self.run_response(response, verifier=lambda c, e: {"claim_id": c["claim_id"], "outcome": "keep"})
        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.removed_claims), 1)

    def test_unexpected_tool_stays_partial(self):
        response = self.response()
        e = envelope("unexpected")
        response["evidence_log"][e["request_id"]] = e
        self.assertEqual(self.run_response(response).status, "partial")

    def test_empty_draft_cannot_be_complete_even_with_successful_tools(self):
        response = self.response()
        response["report_draft"] = []
        result = self.run_response(response)
        self.assertEqual(result.status, "partial")
        self.assertFalse(result.claims)
        self.assertEqual(len(result.tool_results), 1)

    def test_failed_capture_with_stale_refs_cannot_support_keep(self):
        response = self.response()
        captured = next(iter(response["evidence_log"].values()))
        captured.update(status="error", error="query failed")
        # Some providers retain data/refs on an error. Neither becomes proof.
        result = self.run_response(
            response, verifier=lambda c, e: {"claim_id": c["claim_id"], "outcome": "keep"})
        self.assertEqual(result.status, "partial")
        self.assertFalse(result.claims)
        self.assertEqual(result.removed_claims[0]["verifier_outcome"], "remove")

    def test_verifier_failure_still_renders_once_without_replaying_analyst(self):
        response = self.response()
        calls, renders = [], []
        def verifier(claim, evidence):
            raise RuntimeError("synthetic verifier failure")
        result = run_analyst_week(
            "2018-08-13", lambda week: (calls.append(week) or response),
            expected_tool_names=["weekly_revenue"], verifier=verifier,
            renderer=lambda payload: renders.append(payload))
        self.assertEqual(calls, ["2018-08-13"])
        self.assertEqual(len(renders), 1)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.removed_claims[0]["verifier_reason"], "verifier failure")

    def test_duplicate_expected_tool_configuration_never_completes(self):
        result = run_analyst_week(
            "2018-08-13", lambda week: self.response(),
            expected_tool_names=["weekly_revenue", "weekly_revenue"],
            verifier=lambda c, e: {"claim_id": c["claim_id"], "outcome": "keep"})
        self.assertEqual(result.status, "partial")


if __name__ == "__main__":
    unittest.main()

