import json
import unittest
from src.analyst_adapter import run_analyst_week
from src.wbr_contract import ContractError


class AnalystInitializationFailureTests(unittest.TestCase):
    def test_initialization_failure_is_partial_without_retry_or_private_text(self):
        calls = []
        rendered = []
        def failing(week):
            calls.append(week)
            raise RuntimeError("PRIVATE_SECRET_CANARY")
        result = run_analyst_week(
            "2018-08-13", failing, expected_tool_names=("get_weekly_metric",),
            renderer=lambda payload: rendered.append(payload),
        )
        self.assertEqual(calls, ["2018-08-13"])
        self.assertEqual(len(rendered), 1)
        self.assertEqual(result.status, "partial")
        payload = result.renderer_input
        self.assertNotIn("PRIVATE_SECRET_CANARY", json.dumps(payload))
        self.assertEqual(payload["analyst_run"], {"status": "error", "error": "ANALYST_CALL_FAILED"})
        self.assertIsNone(payload["analyst_draft"])

    def test_malformed_return_still_fails_contract_validation(self):
        with self.assertRaises(ContractError):
            run_analyst_week("2018-08-13", lambda _: {}, expected_tool_names=())

    def test_operator_interrupt_is_not_swallowed(self):
        def interrupted(_):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            run_analyst_week("2018-08-13", interrupted, expected_tool_names=())

