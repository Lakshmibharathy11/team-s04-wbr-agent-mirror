"""The opt-in policy is a proposal exercised only with synthetic evidence."""
import unittest
import json
from pathlib import Path
from copy import deepcopy

from src.numeric_verifier import make_captured_verifier
import test_numeric_verifier as numeric
import test_lakshmi_handoff as handoff
from src.analyst_adapter import run_analyst_week


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
            self.assertEqual(proposal(claim, ctx.evidence_log)["outcome"], "keep")
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
        self.assertEqual(result.status, "complete")
        response["report_draft"]["claims"][0]["statement"] = "health_beauty revenue fell 95% in SP."
        result = run_analyst_week("2018-06-04", lambda week: response,
                                 expected_tool_names=handoff.TOOLS, verifier=verifier)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.removed_claims[0]["verifier_outcome"], "remove")

