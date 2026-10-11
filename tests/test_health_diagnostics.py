import json
import unittest
from src.health_diagnostics import check_configuration


class HealthDiagnosticsTests(unittest.TestCase):
    def live_env(self):
        return {"WBR_BACKEND": "athena", "WBR_MODEL_ID": "us.amazon.nova-micro-v1:0"}

    def test_local_defaults_do_not_claim_cloud_acceptance(self):
        result = check_configuration({})
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["aws_runtime_verified"])
        self.assertFalse(result["credits_verified"])
        self.assertFalse(result["spending_authorized"])

    def test_live_defaults_block_local_backend_and_missing_model(self):
        self.assertEqual(check_configuration({}, live=True)["problem_codes"],
                         ["LIVE_REQUIRES_ATHENA", "MODEL_REQUIRES_REVIEW"])

    def test_reviewed_settings_pass_configuration_only(self):
        self.assertEqual(check_configuration(self.live_env(), live=True)["status"], "PASS")

    def test_mistyped_backend_does_not_silently_fall_back(self):
        self.assertIn("INVALID_BACKEND", check_configuration({"WBR_BACKEND": "athenna"})["problem_codes"])

    def test_region_key_is_wbr_aws_region(self):
        env = self.live_env() | {"WBR_AWS_REGION": "us-west-2", "AWS_REGION": "us-east-2"}
        self.assertIn("UNREVIEWED_REGION", check_configuration(env, live=True)["problem_codes"])

    def test_unreviewed_model_requires_review(self):
        env = self.live_env() | {"WBR_MODEL_ID": "another-model"}
        self.assertIn("MODEL_REQUIRES_REVIEW", check_configuration(env, live=True)["problem_codes"])

    def test_unreviewed_data_boundaries_block(self):
        for key, code in [("WBR_ATHENA_WORKGROUP", "UNREVIEWED_WORKGROUP"),
                          ("WBR_ATHENA_DATABASE", "UNREVIEWED_DATABASE")]:
            with self.subTest(key=key):
                self.assertIn(code, check_configuration(self.live_env() | {key: "other"}, live=True)["problem_codes"])

    def test_failure_simulation_blocks_live_only(self):
        env = self.live_env() | {"WBR_SIMULATE_FAILURE": "timeout"}
        self.assertIn("FAILURE_SIMULATION_ENABLED", check_configuration(env, live=True)["problem_codes"])
        self.assertEqual(check_configuration(env)["status"], "PASS")

    def test_environment_and_secrets_never_appear_in_output(self):
        canary = "PRIVATE_SECRET_CANARY"
        env = {key: canary for key in ["AWS_SECRET_ACCESS_KEY", "WBR_MODEL_ID", "WBR_BACKEND",
                                     "WBR_ATHENA_DATABASE", "WBR_SIMULATE_FAILURE", "UNRELATED"]}
        before = dict(env)
        self.assertNotIn(canary, json.dumps(check_configuration(env, live=True)))
        self.assertEqual(env, before)

