import unittest

from src.wbr_runner import run_wbr


EXPECTED_TOOLS = (
    "weekly_revenue",
    "weekly_orders",
    "weekly_review_score",
    "category_breakdown",
    "state_breakdown",
)


def envelope(name, *, status="ok", data=None, refs=None, warnings=None, error=None):
    return {
        "tool_name": name,
        "request_id": f"req-{name}",
        "status": status,
        "generated_at": "2026-10-05T00:00:00+00:00",
        "parameters": {"week": "synthetic-current"},
        "data": {} if data is None else data,
        "evidence_refs": [f"athena:{name}"] if refs is None and status != "error" else (refs or []),
        "warnings": warnings or [],
        "error": error,
    }


def make_tool(name, payload):
    return (name, lambda _params, payload=payload: payload, {"week": "synthetic-current"})


def good_claims(_tool_results):
    return [
        {
            "claim_id": "c1",
            "question_id": "q1",
            "statement": "Revenue declined in the current synthetic week.",
            "claim_type": "fact",
            "confidence": None,
            "supporting_evidence": ["athena:weekly_revenue"],
            "causal_language_allowed": False,
            "requires_human_review": False,
        }
    ]


def trusted_test_verifier(claim, evidence_by_ref):
    supporting = set(claim["supporting_evidence"])
    if supporting and supporting.issubset(set(evidence_by_ref)):
        return {
            "claim_id": claim["claim_id"],
            "outcome": "keep",
            "reason": "trusted synthetic verifier",
        }
    return {
        "claim_id": claim["claim_id"],
        "outcome": "remove",
        "reason": "missing evidence",
    }


class WBRRunnerTests(unittest.TestCase):
    def all_good_tools(self):
        return [make_tool(name, envelope(name)) for name in EXPECTED_TOOLS]

    def test_complete_synthetic_path(self):
        seen = {}

        def renderer(payload):
            seen.update(payload)
            return "rendered"

        result = run_wbr(
            self.all_good_tools(),
            good_claims,
            expected_tool_names=EXPECTED_TOOLS,
            renderer=renderer,
            verifier=trusted_test_verifier,
        )
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.rendered, "rendered")
        self.assertEqual(seen["label"], "SYNTHETIC")

    def test_partial_tool_result_stays_partial_and_preserves_warning(self):
        tools = self.all_good_tools()
        tools[2] = make_tool(
            "weekly_review_score",
            envelope(
                "weekly_review_score",
                status="partial",
                data={"value": None},
                warnings=["review coverage unavailable"],
            ),
        )
        result = run_wbr(tools, good_claims, expected_tool_names=EXPECTED_TOOLS)
        self.assertEqual(result.status, "partial")
        self.assertIn("review coverage unavailable", result.warnings)

    def test_empty_tools_are_partial(self):
        result = run_wbr([], good_claims, expected_tool_names=EXPECTED_TOOLS)
        self.assertEqual(result.status, "partial")

    def test_incomplete_tools_are_partial(self):
        result = run_wbr(
            self.all_good_tools()[:-1],
            good_claims,
            expected_tool_names=EXPECTED_TOOLS,
        )
        self.assertEqual(result.status, "partial")
        self.assertTrue(any("missing expected tools" in warning for warning in result.warnings))

    def test_malformed_tool_retries_once_then_partial(self):
        attempts = {"count": 0}

        def malformed(_params):
            attempts["count"] += 1
            return {"status": "ok"}

        tools = self.all_good_tools()
        tools[0] = ("weekly_revenue", malformed, {})
        result = run_wbr(tools, good_claims, expected_tool_names=EXPECTED_TOOLS)
        self.assertEqual(attempts["count"], 2)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.tool_results[0]["status"], "error")

    def test_retry_success(self):
        attempts = {"count": 0}

        def flaky(_params):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise TimeoutError("timeout")
            return envelope("weekly_revenue")

        tools = self.all_good_tools()
        tools[0] = ("weekly_revenue", flaky, {})
        result = run_wbr(
            tools,
            good_claims,
            expected_tool_names=EXPECTED_TOOLS,
            verifier=trusted_test_verifier,
        )
        self.assertEqual(attempts["count"], 2)
        self.assertEqual(result.status, "partial")
        self.assertTrue(any("retried once" in warning for warning in result.warnings))

    def test_default_verifier_never_keeps_claim_from_reference_membership_alone(self):
        def analyst(_results):
            claim = good_claims(_results)[0]
            claim["statement"] = "Revenue increased 500%."
            return [claim]

        result = run_wbr(
            self.all_good_tools(),
            analyst,
            expected_tool_names=EXPECTED_TOOLS,
        )
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.claims[0]["verifier_outcome"], "flag")
        self.assertEqual(result.claims[0]["verifier_reason"], "semantic verification unavailable")

    def test_analyst_cannot_self_authorize_causal_language(self):
        def analyst(_results):
            claim = good_claims(_results)[0]
            claim["statement"] = "Lower reviews caused the revenue decline."
            claim["causal_language_allowed"] = True
            return [claim]

        result = run_wbr(
            self.all_good_tools(),
            analyst,
            expected_tool_names=EXPECTED_TOOLS,
        )
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.claims[0]["verifier_outcome"], "flag")
        self.assertEqual(result.claims[0]["verifier_reason"], "unsupported causal language")

    def test_missing_evidence_removes_claim_and_partial(self):
        def analyst(_results):
            claim = good_claims(_results)[0]
            claim["supporting_evidence"] = ["athena:not-present"]
            return [claim]

        result = run_wbr(
            self.all_good_tools(),
            analyst,
            expected_tool_names=EXPECTED_TOOLS,
        )
        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.removed_claims), 1)
        self.assertEqual(result.removed_claims[0]["verifier_outcome"], "remove")

    def test_malformed_analyst_response_is_partial(self):
        result = run_wbr(
            self.all_good_tools(),
            lambda _results: {"not": "a claim sequence"},
            expected_tool_names=EXPECTED_TOOLS,
        )
        self.assertEqual(result.status, "partial")
        self.assertTrue(any("analyst response" in warning for warning in result.warnings))

    def test_mixed_valid_and_malformed_claims_stay_partial(self):
        def analyst(_results):
            claims = good_claims(_results)
            claims.append({"claim_id": "broken"})
            return claims

        result = run_wbr(
            self.all_good_tools(),
            analyst,
            expected_tool_names=EXPECTED_TOOLS,
        )
        self.assertEqual(result.status, "partial")
        self.assertTrue(any("rejected as malformed" in warning for warning in result.warnings))

    def test_mismatched_tool_name_retries_then_partial(self):
        attempts = {"count": 0}

        def wrong_tool(_params):
            attempts["count"] += 1
            return envelope("weekly_orders")

        tools = self.all_good_tools()
        tools[0] = ("weekly_revenue", wrong_tool, {})
        result = run_wbr(tools, good_claims, expected_tool_names=EXPECTED_TOOLS)
        self.assertEqual(attempts["count"], 2)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.tool_results[0]["tool_name"], "weekly_revenue")
        self.assertIn("mismatch", result.tool_results[0]["error"])

    def test_duplicate_claim_ids_are_rejected_and_partial(self):
        def analyst(_results):
            supported = good_claims(_results)[0]
            unsupported = dict(supported)
            unsupported["statement"] = "Revenue fell as a result of lower reviews."
            unsupported["supporting_evidence"] = ["athena:not-present"]
            return [unsupported, supported]

        result = run_wbr(
            self.all_good_tools(),
            analyst,
            expected_tool_names=EXPECTED_TOOLS,
        )
        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.claims), 0)
        self.assertEqual(len(result.removed_claims), 1)
        self.assertTrue(any("duplicate claim_id" in warning for warning in result.warnings))

    def test_common_causal_constructions_are_flagged(self):
        for statement in (
            "Revenue fell because reviews fell.",
            "Revenue fell as a result of lower reviews.",
            "Revenue decline was driven by lower reviews.",
            "Revenue decline was attributed to lower reviews.",
            "Revenue decline was stemming from lower reviews.",
        ):
            with self.subTest(statement=statement):
                def analyst(_results, statement=statement):
                    claim = good_claims(_results)[0]
                    claim["statement"] = statement
                    return [claim]

                result = run_wbr(
                    self.all_good_tools(),
                    analyst,
                    expected_tool_names=EXPECTED_TOOLS,
                )
                self.assertEqual(result.status, "partial")
                self.assertEqual(result.claims[0]["verifier_outcome"], "flag")

    def test_whitespace_evidence_ref_retries_then_partial(self):
        attempts = {"count": 0}

        def blank_evidence(_params):
            attempts["count"] += 1
            return envelope("weekly_revenue", refs=["   "])

        tools = self.all_good_tools()
        tools[0] = ("weekly_revenue", blank_evidence, {})
        result = run_wbr(tools, good_claims, expected_tool_names=EXPECTED_TOOLS)
        self.assertEqual(attempts["count"], 2)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.tool_results[0]["status"], "error")

    def test_whitespace_claim_evidence_is_rejected(self):
        def analyst(_results):
            claim = good_claims(_results)[0]
            claim["supporting_evidence"] = ["   "]
            return [claim]

        result = run_wbr(
            self.all_good_tools(),
            analyst,
            expected_tool_names=EXPECTED_TOOLS,
        )
        self.assertEqual(result.status, "partial")
        self.assertTrue(any("supporting_evidence" in warning for warning in result.warnings))

    def test_duplicate_configured_tool_names_force_partial(self):
        tools = self.all_good_tools()
        tools.append(make_tool("weekly_revenue", envelope("weekly_revenue")))
        result = run_wbr(tools, good_claims, expected_tool_names=EXPECTED_TOOLS)
        self.assertEqual(result.status, "partial")
        self.assertTrue(any("duplicate configured tools" in warning for warning in result.warnings))

    def test_unexpected_configured_tool_forces_partial(self):
        tools = self.all_good_tools()
        tools.append(make_tool("extra_tool", envelope("extra_tool")))
        result = run_wbr(
            tools,
            good_claims,
            verifier=trusted_test_verifier,
            expected_tool_names=EXPECTED_TOOLS,
        )
        self.assertEqual(result.status, "partial")
        self.assertTrue(any("unexpected configured tools" in warning for warning in result.warnings))

    def test_non_string_claim_id_is_rejected_and_partial(self):
        def analyst(_results):
            claim = good_claims(_results)[0]
            claim["claim_id"] = None
            return [claim]

        result = run_wbr(
            self.all_good_tools(),
            analyst,
            expected_tool_names=EXPECTED_TOOLS,
        )
        self.assertEqual(result.status, "partial")
        self.assertTrue(any("claim_id must be a non-empty string" in warning for warning in result.warnings))

    def test_trusted_verifier_keep_without_available_evidence_is_removed(self):
        def analyst(_results):
            claim = good_claims(_results)[0]
            claim["supporting_evidence"] = ["athena:not-present"]
            return [claim]

        def verifier(claim, _evidence_by_ref):
            return {
                "claim_id": claim["claim_id"],
                "outcome": "keep",
                "reason": "incorrectly permissive verifier",
            }

        result = run_wbr(
            self.all_good_tools(),
            analyst,
            verifier=verifier,
            expected_tool_names=EXPECTED_TOOLS,
        )
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.removed_claims[0]["verifier_outcome"], "remove")
        self.assertEqual(result.removed_claims[0]["verifier_reason"], "missing or unavailable evidence")

    def test_duplicate_evidence_reference_forces_partial(self):
        tools = self.all_good_tools()
        duplicate_ref = ["athena:shared"]
        tools[0] = make_tool("weekly_revenue", envelope("weekly_revenue", refs=duplicate_ref))
        tools[1] = make_tool("weekly_orders", envelope("weekly_orders", refs=duplicate_ref))

        def analyst(_results):
            claim = good_claims(_results)[0]
            claim["supporting_evidence"] = ["athena:shared"]
            return [claim]

        result = run_wbr(
            tools,
            analyst,
            verifier=trusted_test_verifier,
            expected_tool_names=EXPECTED_TOOLS,
        )
        self.assertEqual(result.status, "partial")
        self.assertTrue(any("duplicate evidence reference" in warning for warning in result.warnings))

    def test_retry_exhaustion_preserves_last_valid_error_envelope(self):
        attempts = {"count": 0}

        def failing(_params):
            attempts["count"] += 1
            return {
                "tool_name": "weekly_review_score",
                "request_id": f"real-request-{attempts['count']}",
                "status": "error",
                "generated_at": f"2026-10-05T00:00:0{attempts['count']}+00:00",
                "parameters": {"week": "real-week"},
                "data": {"diagnostic": attempts["count"]},
                "evidence_refs": [],
                "warnings": [f"tool-warning-{attempts['count']}"],
                "error": f"Athena unavailable {attempts['count']}",
            }

        tools = self.all_good_tools()
        tools[2] = ("weekly_review_score", failing, {})
        result = run_wbr(tools, good_claims, expected_tool_names=EXPECTED_TOOLS)
        self.assertEqual(attempts["count"], 2)
        preserved = result.tool_results[2]
        self.assertEqual(preserved["request_id"], "real-request-2")
        self.assertEqual(preserved["data"], {"diagnostic": 2})
        self.assertEqual(preserved["error"], "Athena unavailable 2")
        self.assertIn("tool-warning-2", preserved["warnings"])
        self.assertIn("retry exhausted", preserved["warnings"])

    def test_error_tool_retry_exhaustion_preserves_failure(self):
        attempts = {"count": 0}

        def failing(_params):
            attempts["count"] += 1
            return envelope(
                "weekly_review_score",
                status="error",
                refs=[],
                error="Athena unavailable",
            )

        tools = self.all_good_tools()
        tools[2] = ("weekly_review_score", failing, {})
        result = run_wbr(tools, good_claims, expected_tool_names=EXPECTED_TOOLS)
        self.assertEqual(attempts["count"], 2)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.tool_results[2]["error"], "Athena unavailable")


if __name__ == "__main__":
    unittest.main()

