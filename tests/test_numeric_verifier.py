import unittest
from copy import deepcopy

from src.numeric_verifier import make_captured_verifier


class NumericVerifierTests(unittest.TestCase):
    def setUp(self):
        self.claim = {"claim_id": "c1", "question_id": "Q3",
                      "statement": "health_beauty revenue fell 95% in SP.",
                      "claim_type": "fact", "confidence": "medium",
                      "supporting_evidence": ["req1"], "causal_language_allowed": False,
                      "requires_human_review": False}
        self.env = {"tool_name": "get_weekly_metric", "status": "ok",
                    "parameters": {"week": "2018-08-13", "metric": "product_revenue",
                                   "state": "SP", "category": "health_beauty"},
                    "data": {"metric": "product_revenue", "current": 10.9,
                             "previous": 100, "change": -89.1, "change_type": "pct"}}
        self.verify = make_captured_verifier("2018-08-13")

    def result(self):
        return self.verify(self.claim, {"req1": self.env})

    def test_contradictory_percentage_removed(self):
        self.assertEqual(self.result()["outcome"], "remove")

    def test_correct_rounded_percentage_does_not_self_authorize_keep(self):
        self.claim["statement"] = "health_beauty revenue fell 89% in SP."
        self.assertEqual(self.result()["outcome"], "flag")
        self.assertIn("matches", self.result()["reason"])

    def test_wrong_direction_removed(self):
        self.claim["statement"] = "health_beauty revenue rose 89.1% in SP."
        self.assertEqual(self.result()["outcome"], "remove")

    def test_other_scope_cannot_disprove_claim(self):
        self.env["parameters"]["state"] = "RJ"
        self.assertEqual(self.result()["reason"], "semantic verification unavailable")

    def test_other_week_cannot_disprove_claim(self):
        self.env["parameters"]["week"] = "2018-08-06"
        self.assertEqual(self.result()["reason"], "semantic verification unavailable")

    def test_other_metric_cannot_disprove_claim(self):
        self.env["data"]["metric"] = "orders"
        self.assertEqual(self.result()["outcome"], "flag")

    def test_invalid_numeric_data_stays_reviewed(self):
        for key, value in [("current", None), ("previous", 0), ("current", True),
                           ("change", float("nan")), ("current", -1), ("change_type", "points")]:
            original = deepcopy(self.env)
            self.env["data"][key] = value
            with self.subTest(key=key, value=value):
                self.assertEqual(self.result()["outcome"], "flag")
            self.env = original

    def test_inconsistent_change_not_used(self):
        self.env["data"]["change"] = -95
        self.assertIn("inconsistent", self.result()["reason"])

    def test_independently_rounded_tool_values_are_not_false_inconsistency(self):
        self.env["data"].update(current=10.91, previous=100.07, change=-89.10)
        self.assertEqual(self.result()["outcome"], "remove")
        self.assertIn("contradicts", self.result()["reason"])
        self.claim["statement"] = "health_beauty revenue fell 89.1% in SP."
        self.assertIn("matches", self.result()["reason"])

    def test_rounding_uncertain_denominator_stays_reviewed(self):
        self.env["data"].update(current=0.001, previous=0.001, change=0)
        self.assertEqual(self.result()["outcome"], "flag")
        self.assertIn("denominator uncertain", self.result()["reason"])

    def test_unrelated_number_does_not_count_as_support(self):
        self.env["data"]["z_score"] = 95
        self.assertEqual(self.result()["outcome"], "remove")

    def test_matching_breakdown_group_selected(self):
        self.env["tool_name"] = "get_breakdown"
        self.env["parameters"].update(dimension="state", state=None)
        series = deepcopy(self.env["data"])
        self.env["data"] = {"metric": "product_revenue", "dimension": "state",
                            "groups": [{**series, "group": "SP"},
                                       {**series, "group": "RJ", "change": -95}]}
        self.assertEqual(self.result()["outcome"], "remove")

    def test_conflicting_captures_not_cherry_picked(self):
        other = deepcopy(self.env)
        other["data"].update(current=5, change=-95)
        self.claim["supporting_evidence"].append("req2")
        result = self.verify(self.claim, {"req1": self.env, "req2": other})
        self.assertEqual(result["outcome"], "flag")
        self.assertIn("conflicting", result["reason"])

    def test_unrecognized_prose_stays_reviewed(self):
        self.claim["statement"] = "Revenue fell 95% and orders rose 5%."
        self.assertEqual(self.result()["outcome"], "flag")

