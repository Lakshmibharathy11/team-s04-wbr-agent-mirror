"""The opt-in policy is a proposal exercised only with synthetic evidence."""
import unittest
import json
from pathlib import Path
from copy import deepcopy

from src.numeric_verifier import make_captured_verifier
import test_numeric_verifier as numeric
import test_lakshmi_handoff as handoff
from src.analyst_adapter import run_analyst_week


class ReviewScorePolicyTests(unittest.TestCase):
    def setUp(self):
        self.claim = {"claim_id": "q2", "question_id": "Q2", "claim_type": "fact",
                      "statement": "average review score rose 0.15 points.",
                      "supporting_evidence": ["r"], "confidence": "medium",
                      "requires_human_review": False, "causal_language_allowed": False}
        self.env = {"tool_name": "get_weekly_metric", "status": "ok", "warnings": [],
                    "parameters": {"week": "2018-08-13", "compare_week": "2018-08-06",
                                   "metric": "avg_review_score", "state": None, "category": None,
                                   "numeric_precision_digits": 2, "filters": {}},
                    "data": {"metric": "avg_review_score", "current": 4.15, "previous": 4.0,
                             "change": .15, "change_type": "points", "current_base": 100,
                             "previous_base": 100, "baseline": {"weeks": 8}, "z_score": 2.0}}

    def result(self, enabled=True):
        return make_captured_verifier("2018-08-13", allow_verified_facts=enabled)(
            self.claim, {"r": self.env})

    def test_point_fact_requires_explicit_policy(self):
        self.assertEqual(self.result(False)["outcome"], "flag")
        self.assertEqual(self.result()["outcome"], "keep")

    def test_wrong_direction_and_amount_removed(self):
        for statement in ["average review score fell 0.15 points.",
                          "average review score rose 0.25 points."]:
            self.claim["statement"] = statement
            self.assertEqual(self.result()["outcome"], "remove")

    def test_percent_and_unusualness_not_accepted_as_points(self):
        for statement in ["average review score rose 0.15%.",
                          "average review score rose 0.15 points, which is unusual."]:
            self.claim["statement"] = statement
            self.assertEqual(self.result()["outcome"], "flag")

    def test_invalid_or_inconsistent_scores_stay_reviewed(self):
        for key, value in [("current", 6), ("previous", 0), ("current", True),
                           ("change", float("nan")), ("change", .25), ("change_type", "pct")]:
            old = deepcopy(self.env)
            self.env["data"][key] = value
            self.assertEqual(self.result()["outcome"], "flag")
            self.env = old

    def test_quality_and_review_requirement_preserved(self):
        self.claim["requires_human_review"] = True
        self.assertEqual(self.result()["outcome"], "flag")
        self.claim["requires_human_review"] = False
        self.env["data"]["current_base"] = 29
        self.assertEqual(self.result()["outcome"], "flag")
        self.env["data"]["current_base"] = 100
        self.env["warnings"] = [{"code": "SMALL_SAMPLE"}]
        self.assertEqual(self.result()["outcome"], "flag")

    def test_wrong_week_metric_and_slice_do_not_lend_support(self):
        for key, value in [("week", "2018-08-20"), ("metric", "one_star_pct"), ("state", "SP")]:
            old = deepcopy(self.env)
            self.env["parameters"][key] = value
            self.assertEqual(self.result()["outcome"], "flag")
            self.env = old
        self.env["parameters"]["compare_week"] = "2018-07-30"
        self.assertEqual(self.result()["outcome"], "flag")

    def test_excessive_precision_cannot_claim_certainty(self):
        self.claim["statement"] = "average review score rose 0.150000 points."
        self.assertEqual(self.result()["outcome"], "flag")

    def test_conflicting_captures_cannot_be_cherry_picked(self):
        other = deepcopy(self.env)
        other["data"].update(current=4.25, change=.25)
        self.claim["supporting_evidence"].append("r2")
        result = make_captured_verifier("2018-08-13", allow_verified_facts=True)(
            self.claim, {"r": self.env, "r2": other})
        self.assertEqual(result["outcome"], "flag")


class VerifiedFactPolicyTests(unittest.TestCase):
    def test_recorded_atomic_facts_reconcile_with_postchecks_without_rewriting_capture(self):
        from agent.postchecks import finalize
        from agent.run_context import RunContext
        from agent.schema import DraftClaim, DraftReport

        path = Path(__file__).resolve().parents[1] / "runs" / "eval_20261010_115418.json"
        original = path.read_bytes()
        saved = json.loads(original.decode("utf-8-sig"))
        scenario = next(s for s in saved["results"] if s["scenario"] == "S1_category_drop")
        ctx = RunContext(scenario["week"])
        ctx.evidence_log = deepcopy(scenario["runs"][0]["evidence_log"])
        snapshot = deepcopy(ctx.evidence_log)
        specs = [("product_revenue", None, "Q1", "revenue fell 15.32%."),
                 ("orders", None, "Q1", "orders fell 9.89%."),
                 ("avg_review_score", None, "Q2", "average review score rose 0.15 points."),
                 ("product_revenue", "health_beauty", "Q3", "health_beauty revenue fell 49.6%.")]
        claims = []
        for metric, category, question, statement in specs:
            rid = next(rid for rid, env in ctx.evidence_log.items()
                       if env["tool_name"] == "get_weekly_metric"
                       and env["parameters"].get("metric") == metric
                       and env["parameters"].get("category") == category
                       and env["parameters"].get("state") is None)
            claims.append(DraftClaim(question_id=question, statement=statement,
                                     claim_type="fact", supporting_evidence=[rid]))
        # Diagnostic assertions only: not a replacement model report or a COMPLETE demo.
        draft = finalize(DraftReport(claims=claims, actionable_questions=[], unavailable=[]), ctx)
        default = make_captured_verifier(ctx.week)
        proposal = make_captured_verifier(ctx.week, allow_verified_facts=True)
        for claim in draft["claims"]:
            self.assertFalse(claim["requires_human_review"])
            self.assertEqual(claim["confidence"], "medium")
            self.assertEqual(default(claim, ctx.evidence_log)["outcome"], "flag")
            self.assertEqual(proposal(claim, ctx.evidence_log)["outcome"], "flag")  # undeclared historical precision
            requires_review = {**claim, "requires_human_review": True}
            self.assertEqual(proposal(requires_review, ctx.evidence_log)["outcome"], "flag")
        self.assertEqual(ctx.evidence_log, snapshot)
        self.assertEqual(path.read_bytes(), original)

    def setUp(self):
        fixture = numeric.NumericVerifierTests()
        fixture.setUp()
        self.claim, self.env = fixture.claim, fixture.env
        self.claim["statement"] = "health_beauty revenue fell 89% in SP."
        self.env["warnings"] = []
        self.env["parameters"]["compare_week"] = "2018-08-06"
        self.env["parameters"]["numeric_precision_digits"] = 2
        self.env["parameters"]["filters"] = {"state_group": "SP", "category_group": "health_beauty"}
        self.env["data"].update(current_base=100, previous_base=100,
                                baseline={"weeks": 8}, z_score=-2)
        self.verify = make_captured_verifier("2018-08-13", allow_verified_facts=True)

    def outcome(self):
        return self.verify(self.claim, {"req1": self.env})["outcome"]

    def test_default_policy_still_requires_review(self):
        result = make_captured_verifier("2018-08-13")(self.claim, {"req1": self.env})
        self.assertEqual(result["outcome"], "flag")

    def test_explicit_policy_can_keep_exact_fact(self):
        self.assertEqual(self.outcome(), "keep")

    def test_high_confidence_requires_scoped_signal(self):
        self.claim["confidence"] = "high"
        self.assertEqual(self.outcome(), "flag")
        self.env["data"]["z_score"] = -3
        self.assertEqual(self.outcome(), "keep")

    def test_review_requirement_cannot_be_waived(self):
        self.claim["requires_human_review"] = True
        self.assertEqual(self.outcome(), "flag")

    def test_weak_or_missing_quality_never_kept(self):
        for key, value in [("current_base", 29), ("previous_base", True),
                           ("baseline", {"weeks": 7}), ("z_score", None),
                           ("warnings", ["SMALL_SAMPLE"])]:
            original = deepcopy(self.env)
            self.env["data"][key] = value
            with self.subTest(key=key):
                self.assertEqual(self.outcome(), "flag")
            self.env = original

    def test_partial_capture_never_kept(self):
        self.env["status"] = "partial"
        self.assertEqual(self.outcome(), "flag")

    def test_wrong_comparison_week_never_kept(self):
        self.env["parameters"]["compare_week"] = "2018-07-30"
        self.assertEqual(self.outcome(), "flag")

    def test_envelope_warning_never_kept(self):
        self.env["warnings"] = [{"code": "INCOMPLETE_WEEK", "message": "not final"}]
        self.assertEqual(self.outcome(), "flag")

    def test_extra_unrelated_citation_cannot_lend_confidence(self):
        self.claim["supporting_evidence"].append("req2")
        other = deepcopy(self.env)
        other["parameters"]["state"] = "RJ"
        other["data"]["z_score"] = -100
        result = self.verify(self.claim, {"req1": self.env, "req2": other})
        self.assertEqual(result["outcome"], "flag")

    def test_self_authorized_causal_permission_never_kept(self):
        self.claim["causal_language_allowed"] = True
        self.assertEqual(self.outcome(), "flag")

    def test_too_precise_claim_not_proved_by_rounded_capture(self):
        self.claim["statement"] = "health_beauty revenue fell 89.100000% in SP."
        self.assertEqual(self.outcome(), "flag")

    def test_integrated_opt_in_complete_and_partial(self):
        response = handoff.LakshmiHandoffTests().response()
        self.env.update(request_id="req-get_weekly_metric", generated_at="2026-10-07T00:00:00+00:00",
                        evidence_refs=response["evidence_log"]["req-get_weekly_metric"]["evidence_refs"], error=None)
        self.env["parameters"]["week"] = "2018-06-04"
        self.env["parameters"]["compare_week"] = "2018-05-28"
        response["evidence_log"]["req-get_weekly_metric"] = self.env
        self.claim["supporting_evidence"] = ["req-get_weekly_metric"]
        response["report_draft"]["claims"] = [self.claim]
        verifier = make_captured_verifier("2018-06-04", allow_verified_facts=True)
        result = run_analyst_week("2018-06-04", lambda week: response,
                                 expected_tool_names=handoff.TOOLS, verifier=verifier)
        self.assertEqual(result.status, "partial")
        response["report_draft"]["claims"][0]["statement"] = "health_beauty revenue fell 95% in SP."
        result = run_analyst_week("2018-06-04", lambda week: response,
                                 expected_tool_names=handoff.TOOLS, verifier=verifier)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.removed_claims[0]["verifier_outcome"], "remove")



class ReviewRemediationTests(unittest.TestCase):
    def setUp(self):
        fixture = VerifiedFactPolicyTests()
        fixture.setUp()
        self.claim, self.env = fixture.claim, fixture.env
        self.verify = make_captured_verifier("2018-08-13", allow_verified_facts=True)

    def outcome(self):
        return self.verify(self.claim, {"req1": self.env})["outcome"]

    def test_zero_magnitude_never_kept(self):
        self.claim["statement"] = "health_beauty revenue fell 0% in SP."
        self.env["data"].update(current=99.7, previous=100, change=-0.3)
        self.assertEqual(self.outcome(), "remove")

    def test_category_orders_cannot_use_ambiguous_wording(self):
        self.claim["statement"] = "health_beauty orders fell 89% in SP."
        self.env["parameters"]["metric"] = self.env["data"]["metric"] = "orders"
        self.assertEqual(self.outcome(), "flag")

    def test_disagreeing_or_extra_filters_are_unscoped(self):
        for filters in [{"state_group": "RJ", "category_group": "health_beauty"},
                        {"state_group": "SP", "category_group": "health_beauty", "extra": "x"}]:
            self.env["parameters"]["filters"] = filters
            self.assertEqual(self.outcome(), "flag")

    def test_precision_must_be_declared_by_capture(self):
        for precision in [None, 1, True]:
            self.env["parameters"]["numeric_precision_digits"] = precision
            self.assertEqual(self.outcome(), "flag")

    def test_shared_threshold_change_blocks_old_quality(self):
        from tools import config
        original = config.MIN_ORDERS
        try:
            config.MIN_ORDERS = 101
            self.assertEqual(self.outcome(), "flag")
        finally:
            config.MIN_ORDERS = original

    def test_missing_draft_period_cannot_reach_permissive_verifier(self):
        for key in ["week", "compare_week"]:
            response = handoff.LakshmiHandoffTests().response()
            del response["report_draft"][key]
            calls = []
            def keep(c, e):
                calls.append(c)
                return {"claim_id": c["claim_id"], "outcome": "keep"}
            result = run_analyst_week("2018-06-04", lambda _: response,
                                     expected_tool_names=handoff.TOOLS, verifier=keep)
            self.assertEqual(result.status, "partial")
            self.assertFalse(calls)
            self.assertFalse(result.claims)

    def test_one_verified_fact_is_not_complete_report(self):
        response = handoff.LakshmiHandoffTests().response()
        result = run_analyst_week("2018-06-04", lambda _: response,
                                 expected_tool_names=handoff.TOOLS,
                                 verifier=handoff.LakshmiHandoffTests.verifier)
        self.assertEqual(result.claims[0]["verifier_outcome"], "keep")
        self.assertEqual(result.status, "partial")
        self.assertTrue(any("coverage verification unavailable" in w for w in result.warnings))

    def test_runner_cannot_hide_uncited_envelope_warning(self):
        response = handoff.LakshmiHandoffTests().response()
        response["evidence_log"]["req-get_delivery_signals"]["warnings"] = ["SMALL_SAMPLE"]
        result = run_analyst_week("2018-06-04", lambda _: response,
                                 expected_tool_names=handoff.TOOLS,
                                 verifier=handoff.LakshmiHandoffTests.verifier)
        self.assertFalse(result.renderer_input["selected_claims_verified"])
        self.assertIn("SMALL_SAMPLE", result.warnings)
        self.assertEqual(result.status, "partial")

    def test_point_zero_never_kept(self):
        f = ReviewScorePolicyTests(); f.setUp()
        f.claim["statement"] = "average review score rose 0 points."
        f.env["data"].update(current=4.3, previous=4.0, change=0.3)
        self.assertEqual(f.result()["outcome"], "remove")


class ExclusionManifestTests(unittest.TestCase):
    def test_exact_exclusions_leave_non_database_test_enabled(self):
        from scripts.verify_offline_review import EXCLUDED, ExactDatabaseExclusions
        from types import SimpleNamespace
        observed = []
        items = [SimpleNamespace(nodeid=n) for n in EXCLUDED]
        wanted = "tests/test_week_and_wording.py::test_postcheck_flags_evidence_for_another_week"
        items.append(SimpleNamespace(nodeid=wanted))
        hook = SimpleNamespace(pytest_deselected=lambda items: observed.extend(items))
        ExactDatabaseExclusions().pytest_collection_modifyitems(None, SimpleNamespace(hook=hook), items)
        self.assertEqual([i.nodeid for i in items], [wanted])
        self.assertEqual({i.nodeid for i in observed}, EXCLUDED)
        self.assertEqual(len(observed), 10)

    def test_missing_exclusion_fails_instead_of_silently_changing_count(self):
        import pytest
        from scripts.verify_offline_review import ExactDatabaseExclusions
        with self.assertRaises(pytest.UsageError):
            ExactDatabaseExclusions().pytest_collection_modifyitems(None, None, [])


class PrecisionMismatchReviewTests(unittest.TestCase):
    def test_mismatched_percentage_precision_flags_before_contradiction(self):
        f = VerifiedFactPolicyTests(); f.setUp()
        f.claim["statement"] = "health_beauty revenue fell 15.34% in SP."
        f.env["parameters"]["numeric_precision_digits"] = 1
        f.env["data"].update(current=84.7, previous=100, change=-15.3)
        for enabled in [False, True]:
            with self.subTest(enabled=enabled):
                result = make_captured_verifier("2018-08-13", allow_verified_facts=enabled)(f.claim, {"req1": f.env})
                self.assertEqual(result["outcome"], "flag")
                self.assertIn("precision", result["reason"])

    def test_mismatched_point_precision_flags_before_contradiction(self):
        f = ReviewScorePolicyTests(); f.setUp()
        f.claim["statement"] = "average review score rose 0.24 points."
        f.env["parameters"]["numeric_precision_digits"] = 1
        f.env["data"].update(current=4.2, previous=4.0, change=.2)
        for enabled in [False, True]:
            with self.subTest(enabled=enabled):
                self.assertEqual(f.result(enabled)["outcome"], "flag")

    def test_missing_filters_cannot_keep_fact(self):
        f = VerifiedFactPolicyTests(); f.setUp()
        del f.env["parameters"]["filters"]
        self.assertEqual(f.outcome(), "flag")
