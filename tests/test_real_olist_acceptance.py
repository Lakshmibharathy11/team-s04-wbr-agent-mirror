import unittest

from demo.run_real_olist_acceptance import (
    index_tool_results,
    make_answer_key_verifier,
    require_expected_status,
)


class RealOlistAcceptanceTests(unittest.TestCase):
    def test_duplicate_tool_results_are_rejected_before_indexing(self):
        results = [
            {"tool_name": "weekly_revenue"},
            {"tool_name": "weekly_revenue"},
        ]
        with self.assertRaisesRegex(ValueError, "duplicate tool result"):
            index_tool_results(results)

    def test_answer_key_evidence_must_be_cited_by_claim(self):
        verifier = make_answer_key_verifier(
            {
                "c1": {
                    "outcome": "keep",
                    "required_evidence_refs": ["athena:weekly_revenue"],
                }
            }
        )
        claim = {
            "claim_id": "c1",
            "supporting_evidence": ["athena:weekly_orders"],
        }
        evidence_by_ref = {
            "athena:weekly_revenue": {"tool_name": "weekly_revenue"},
            "athena:weekly_orders": {"tool_name": "weekly_orders"},
        }

        outcome = verifier(claim, evidence_by_ref)

        self.assertEqual(outcome["outcome"], "remove")
        self.assertEqual(
            outcome["reason"],
            "claim does not cite required answer-key evidence",
        )

    def test_answer_key_evidence_must_also_be_available(self):
        verifier = make_answer_key_verifier(
            {
                "c1": {
                    "outcome": "keep",
                    "required_evidence_refs": ["athena:weekly_revenue"],
                }
            }
        )
        claim = {
            "claim_id": "c1",
            "supporting_evidence": ["athena:weekly_revenue"],
        }

        outcome = verifier(claim, {})

        self.assertEqual(outcome["outcome"], "remove")
        self.assertEqual(outcome["reason"], "answer-key evidence unavailable")

    def test_planted_real_olist_requires_complete(self):
        require_expected_status("PLANTED_REAL_OLIST", "complete")
        with self.assertRaisesRegex(ValueError, "expected complete, got partial"):
            require_expected_status("PLANTED_REAL_OLIST", "partial")

    def check_statement(self, actual, expected="Revenue declined 20%.", outcome="keep"):
        verifier = make_answer_key_verifier({"c1": {
            "outcome": outcome,
            "required_evidence_refs": ["athena:weekly_revenue"],
            "expected_statement": expected,
        }})
        return verifier({"claim_id": "c1", "statement": actual,
                         "supporting_evidence": ["athena:weekly_revenue"]},
                        {"athena:weekly_revenue": {"tool_name": "weekly_revenue"}})

    def test_correct_id_and_refs_cannot_keep_wrong_statement(self):
        result = self.check_statement("Revenue increased 500%.")
        self.assertEqual(result["outcome"], "remove")
        self.assertEqual(result["reason"], "claim statement differs from trusted answer key")

    def test_exact_trusted_statement_can_be_kept(self):
        self.assertEqual(self.check_statement("Revenue declined 20%.")["outcome"], "keep")

    def test_absent_blank_and_nontext_answer_assertions_deny(self):
        for expected in (None, "", "   ", 20):
            with self.subTest(expected=expected):
                result = self.check_statement("Revenue declined 20%.", expected=expected)
                self.assertEqual(result["outcome"], "remove")
                self.assertEqual(result["reason"], "answer key has no exact expected statement")

    def test_invalid_outcome_cannot_bypass_answer_contract(self):
        result = self.check_statement("Revenue declined 20%.", outcome="APPROVED")
        self.assertEqual(result["outcome"], "remove")
        self.assertEqual(result["reason"], "answer key has invalid verifier outcome")

    def test_incomplete_real_olist_requires_partial(self):
        require_expected_status("REAL_OLIST_INCOMPLETE", "partial")
        with self.assertRaisesRegex(ValueError, "expected partial, got complete"):
            require_expected_status("REAL_OLIST_INCOMPLETE", "complete")


if __name__ == "__main__":
    unittest.main()


