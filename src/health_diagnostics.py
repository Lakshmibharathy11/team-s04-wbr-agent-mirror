"""Offline configuration diagnostics; never contacts AWS or prints environment values."""
from collections.abc import Mapping


def check_configuration(env: Mapping[str, str], *, live: bool = False) -> dict:
    problems = []
    backend = env.get("WBR_BACKEND", "duckdb")
    if backend not in {"duckdb", "athena"}:
        problems.append("INVALID_BACKEND")
    if live:
        if backend != "athena":
            problems.append("LIVE_REQUIRES_ATHENA")
        if env.get("WBR_AWS_REGION", "us-east-2") != "us-east-2":
            problems.append("UNREVIEWED_REGION")
        if env.get("WBR_MODEL_ID") != "us.amazon.nova-micro-v1:0":
            problems.append("MODEL_REQUIRES_REVIEW")
        if env.get("WBR_ATHENA_WORKGROUP", "S04-WBR") != "S04-WBR":
            problems.append("UNREVIEWED_WORKGROUP")
        if env.get("WBR_ATHENA_DATABASE", "s04_wbr") != "s04_wbr":
            problems.append("UNREVIEWED_DATABASE")
        if env.get("WBR_SIMULATE_FAILURE", ""):
            problems.append("FAILURE_SIMULATION_ENABLED")
    return {
        "check": "OFFLINE_CONFIGURATION_ONLY",
        "status": "BLOCKED" if problems else "PASS",
        "problem_codes": problems,
        "aws_runtime_verified": False,
        "credits_verified": False,
        "spending_authorized": False,
    }
