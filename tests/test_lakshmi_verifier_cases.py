"""Replay actual authored claims against synthetic captures, not real Olist.

Source: work/Lakshmi-data-tools/tests/golden/answer_keys.json
blob 0dfe5a8094d58383eed7f1cc063dd8b7c1b46efa.
V2's desired remove remains an acceptance gap: fallback flags, never keeps.
"""
import unittest

from src.analyst_adapter import run_analyst_week
import test_lakshmi_handoff as handoff


class LakshmiVerifierCaseTests(unittest.TestCase):
    def replay(self, statement, claim_type="fact", failed=False):
        response = handoff.LakshmiHandoffTests().response()
        claim = response["report_draft"]["claims"][0]
        claim.update(statement=statement, claim_type=claim_type,
                     causal_language_allowed=False)
        if failed:
            response["evidence_log"]["req-get_weekly_metric"].update(
                status="error", data=None, evidence_refs=[],
                error={"type": "timeout", "message": "synthetic review timeout"})
        result = run_analyst_week("2018-06-04", lambda week: response,
                                  expected_tool_names=handoff.TOOLS)
        self.assertEqual(result.status, "partial")
        return (result.claims or result.removed_claims)[0]

    def test_v1_unsupported_cause_is_flagged(self):
        claim = self.replay("Average review score fell in RJ because late deliveries rose.",
                            "observed_pattern")
        self.assertEqual(claim["verifier_outcome"], "flag")
        self.assertEqual(claim["verifier_reason"], "unsupported causal language")

    def test_v2_without_scoped_numeric_capture_stays_flagged(self):
        claim = self.replay("health_beauty revenue fell 95% in SP.")
        self.assertEqual(claim["verifier_outcome"], "flag")
        self.assertEqual(claim["verifier_reason"], "semantic verification unavailable")

    def test_v2_with_scoped_numeric_capture_is_removed(self):
        response = handoff.LakshmiHandoffTests().response()
        response["report_draft"]["claims"][0]["statement"] = "health_beauty revenue fell 95% in SP."
        env = response["evidence_log"]["req-get_weekly_metric"]
        env["parameters"].update(metric="product_revenue", state="SP", category="health_beauty")
        env["data"] = {"metric": "product_revenue", "current": 10.9, "previous": 100,
                       "change": -89.1, "change_type": "pct"}
        result = run_analyst_week("2018-06-04", lambda week: response,
                                 expected_tool_names=handoff.TOOLS)
        self.assertEqual(result.removed_claims[0]["verifier_outcome"], "remove")

    def test_v3_failed_review_evidence_is_removed(self):
        claim = self.replay("Average review score was stable this week.", failed=True)
        self.assertEqual(claim["verifier_outcome"], "remove")

    def test_v4_unrelated_metrics_causal_link_is_flagged(self):
        claim = self.replay("Revenue fell because reviews fell.", "observed_pattern")
        self.assertEqual(claim["verifier_outcome"], "flag")
        self.assertEqual(claim["verifier_reason"], "unsupported causal language")

