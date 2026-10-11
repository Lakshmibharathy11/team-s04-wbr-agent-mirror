import unittest

from src.wbr_contract import (
    ContractError,
    Metric,
    assert_unique_order_ids,
    external_to_metric,
    guard_noncausal,
    mean_review,
    metric_to_external,
    pct_change,
    reconcile_total,
    report_status,
    safe_ratio,
    unavailable_metric,
)


class WBRContractTests(unittest.TestCase):
    def test_known_totals(self):
        self.assertAlmostEqual(pct_change(1000, 800), -20.0)
        self.assertAlmostEqual(pct_change(4, 4), 0.0)
        self.assertAlmostEqual(mean_review([5, 4, 4, 3]), 4.0)
        self.assertAlmostEqual(mean_review([4, 3, 3, 2]), 3.0)

    def test_missing_reviews_ignored(self):
        self.assertAlmostEqual(mean_review([5, None, 3]), 4.0)
        self.assertIsNone(mean_review([None, None]))

    def test_zero_denominator_ratio_returns_none(self):
        self.assertIsNone(safe_ratio(5, 0))
        self.assertAlmostEqual(safe_ratio(5, 2), 2.5)

    def test_multi_item_join_safety(self):
        with self.assertRaises(ContractError):
            assert_unique_order_ids([{"order_id": "o1"}, {"order_id": "o1"}])

    def test_reconciliation(self):
        reconcile_total(800.0, [300.0, 250.0, 200.0, 50.0], "category_revenue")
        with self.assertRaises(ContractError):
            reconcile_total(800.0, [300.0, 250.0, 200.0], "category_revenue")

    def test_common_causal_claims_blocked(self):
        for statement in (
            "Revenue fell because reviews fell.",
            "Revenue fell because of lower reviews.",
            "Revenue fell as a result of lower reviews.",
            "Revenue decline was driven by lower reviews.",
            "Revenue decline was attributed to lower reviews.",
            "Revenue decline was stemming from lower reviews.",
            "Lower reviews caused the revenue decline.",
            "Revenue decline resulted from lower reviews.",
            "Lower reviews cause revenue to decline.",
            "Lower reviews are causing revenue to decline.",
            "Revenue declines resulting from lower reviews.",
        ):
            with self.subTest(statement=statement):
                with self.assertRaises(ContractError):
                    guard_noncausal(statement)
        guard_noncausal("Revenue and review score both declined.")

    def test_noncausal_disclaimer_allowed_and_tentative_causal_claim_reviewed(self):
        guard_noncausal("Revenue and review score both declined; this report does not establish a causal relationship.")
        with self.assertRaises(ContractError):
            guard_noncausal("Could lower review scores have contributed to the revenue change?")

    def test_disclaimer_cannot_exempt_another_causal_assertion(self):
        with self.assertRaises(ContractError):
            guard_noncausal("This report does not establish every cause. Revenue fell because reviews fell.")

    def test_question_mark_cannot_exempt_causal_assertion(self):
        with self.assertRaises(ContractError):
            guard_noncausal("Revenue fell because reviews fell. Agree?")

    def test_question_with_causal_wording_is_conservatively_reviewed(self):
        with self.assertRaises(ContractError):
            guard_noncausal("Did revenue fall because reviews fell?")

    def test_drove_is_causal_language(self):
        with self.assertRaises(ContractError):
            guard_noncausal("Lower reviews drove the revenue decline.")

    def test_failure_produces_partial(self):
        metrics = [
            Metric("revenue", 800.0, evidence_ref="athena:q-revenue"),
            Metric("orders", 4.0, evidence_ref="athena:q-orders"),
            unavailable_metric("review_score", "timeout"),
        ]
        self.assertEqual(report_status(metrics), "PARTIAL")

    def test_empty_metric_set_is_partial(self):
        self.assertEqual(report_status([]), "PARTIAL")

    def test_incomplete_metric_set_is_partial(self):
        metrics = [
            Metric("revenue", 800.0, evidence_ref="athena:q-revenue"),
            Metric("orders", 4.0, evidence_ref="athena:q-orders"),
        ]
        self.assertEqual(report_status(metrics), "PARTIAL")

    def test_duplicate_metric_names_are_partial(self):
        metrics = [
            Metric("revenue", 800.0, evidence_ref="athena:q-revenue-a"),
            Metric("revenue", 900.0, evidence_ref="athena:q-revenue-b"),
            Metric("orders", 4.0, evidence_ref="athena:q-orders"),
            Metric("review_score", 3.0, evidence_ref="athena:q-review"),
        ]
        self.assertEqual(report_status(metrics), "PARTIAL")

    def test_whitespace_evidence_is_partial(self):
        metrics = [
            Metric("revenue", 800.0, evidence_ref="   "),
            Metric("orders", 4.0, evidence_ref="athena:q-orders"),
            Metric("review_score", 3.0, evidence_ref="athena:q-review"),
        ]
        self.assertEqual(report_status(metrics), "PARTIAL")

    def test_invalid_metric_values_are_partial(self):
        metrics = [
            Metric("revenue", float("nan"), evidence_ref="athena:q-revenue"),
            Metric("orders", 4.0, evidence_ref="athena:q-orders"),
            Metric("review_score", 3.0, evidence_ref="athena:q-review"),
        ]
        self.assertEqual(report_status(metrics), "PARTIAL")

        metrics[0] = Metric("revenue", "not-a-number", evidence_ref="athena:q-revenue")
        self.assertEqual(report_status(metrics), "PARTIAL")

    def test_missing_evidence_is_partial(self):
        metrics = [
            Metric("revenue", 800.0, evidence_ref="athena:q-revenue"),
            Metric("orders", 4.0, evidence_ref="athena:q-orders"),
            Metric("review_score", 3.0),
        ]
        self.assertEqual(report_status(metrics), "PARTIAL")

    def test_complete_requires_expected_valid_metrics(self):
        metrics = [
            Metric("revenue", 800.0, evidence_ref="athena:q-revenue"),
            Metric("orders", 4.0, evidence_ref="athena:q-orders"),
            Metric("review_score", 3.0, evidence_ref="athena:q-review"),
        ]
        self.assertEqual(report_status(metrics), "COMPLETE")

    def test_explicit_external_adapter(self):
        metric = Metric("revenue", 800.0, evidence_ref="athena:q-revenue")
        external = metric_to_external(metric)
        self.assertEqual(external["status"], "ok")
        self.assertEqual(external["evidence_refs"], ["athena:q-revenue"])
        restored = external_to_metric(external)
        self.assertEqual(restored.status, "OK")
        self.assertEqual(restored.evidence_ref, "athena:q-revenue")


if __name__ == "__main__":
    unittest.main()
