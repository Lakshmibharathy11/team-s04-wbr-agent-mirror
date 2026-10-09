"""Evaluate the analyst agent on the golden scenarios.

    python scripts/evaluate_agent.py                      # all scenarios, 3 trials each
    python scripts/evaluate_agent.py --trials 5 --scenarios S1 S2

Each scenario runs several times because the model is not deterministic. Reports:
- pass rate:  share of trials that passed
- pass@k:     at least one of k trials passed (can it do it?)
- pass^k:     all k trials passed (does it do it reliably?)

Needs data/golden.duckdb, WBR_MODEL_ID, and AWS credentials for Bedrock.
Saves runs/eval_<time>.json with every report and, for failed trials, the transcript.

Cost guards:
    --plan       print the scenarios, limits and worst-case cost; calls nothing
    per run:     at most 25 tool calls and WBR_MAX_RUN_TOKENS tokens (default 300,000)
    per batch:   Athena attempts capped at runs x 25 tool calls x 2 (retry)
    on error:    the batch stops at the first failed run (e.g. no model access),
                 so one setup problem cannot repeat across every scenario
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.analyst import MAX_OUTPUT_TOKENS, MAX_RUN_TOKENS, MAX_TOOL_CALLS, run_analyst  # noqa: E402
from agent.evaluation import GRADERS, grade_run  # noqa: E402
from tools import backend, config           # noqa: E402

# Price per million tokens, for the estimate only. Defaults: Amazon Nova Micro
# ($0.035 input / $0.14 output). Check the Bedrock pricing page for the model you use.
PRICE_IN = float(os.environ.get("WBR_PRICE_IN_PER_M", "0.035"))
PRICE_OUT = float(os.environ.get("WBR_PRICE_OUT_PER_M", "0.14"))
# Largest possible single model call (Nova Micro context window: 128,000 tokens)
CONTEXT_TOKENS = int(os.environ.get("WBR_CONTEXT_TOKENS", "128000"))


def token_cost(input_tokens: int, output_tokens: int) -> float:
    return input_tokens / 1e6 * PRICE_IN + output_tokens / 1e6 * PRICE_OUT


def select(only: list[str]) -> list[dict]:
    keys = json.loads((ROOT / "tests" / "golden" / "answer_keys.json").read_text())
    return [s for s in keys["scenarios"] if s["id"] in GRADERS
            and (not only or any(s["id"].startswith(o) for o in only))]


def plan(trials: int, only: list[str]) -> dict:
    scenarios = select(only)
    runs = len(scenarios) * trials
    # Worst case per run: the token cap plus two calls past it (see agent/steering.py),
    # each at most one full context window; priced as if every token were output (the dearer rate)
    worst_tokens = MAX_RUN_TOKENS + 2 * CONTEXT_TOKENS
    athena_cap = runs * MAX_TOOL_CALLS * (config.MAX_RETRIES + 1)
    return {
        "scenarios": [f"{s['id']} ({s['week']})" for s in scenarios],
        "runs": runs, "max_tool_calls_per_run": MAX_TOOL_CALLS,
        "max_tokens_per_run": MAX_RUN_TOKENS, "max_output_tokens_per_call": MAX_OUTPUT_TOKENS,
        "worst_case_bedrock_usd": round(runs * token_cost(0, worst_tokens), 4),
        "athena_attempt_cap": athena_cap if config.BACKEND == "athena" else 0,
        "worst_case_athena_usd": round(athena_cap * 10 / 1024 ** 2 * 5, 4) if config.BACKEND == "athena" else 0,
    }


def evaluate(trials: int, only: list[str], model_factory=None) -> dict:
    config.DUCKDB_PATH = str(ROOT / "data" / "golden.duckdb")  # golden data on DuckDB
    if config.BACKEND == "athena":
        config.TABLE_PREFIX = "golden_"  # golden data on Athena: golden_weekly_metrics, ...
    scenarios = select(only)
    if config.BACKEND == "athena" and config.MAX_ATHENA_QUERIES is None:
        config.MAX_ATHENA_QUERIES = len(scenarios) * trials * MAX_TOOL_CALLS * (config.MAX_RETRIES + 1)
    results, stopped = [], None
    for key in scenarios:
        if stopped:
            break
        simulate = key.get("run_with", {}).get("WBR_SIMULATE_FAILURE", "")
        trials_out = []
        for t in range(1, trials + 1):
            config.SIMULATE_FAILURE = simulate
            try:
                model = model_factory(key, t) if model_factory else None
                out = run_analyst(key["week"], model=model)
            finally:
                config.SIMULATE_FAILURE = ""
            graded = grade_run(out, key)
            usage = out["run"].get("usage", {})
            trials_out.append({"trial": t, **graded, "usage": usage, "run_status": out["run"]["status"],
                               "run_error": out["run"].get("error"),
                               "report_draft": out["report_draft"],
                               "transcript": None if graded["passed"] else out.get("transcript")})
            mark = "PASS" if graded["passed"] else "FAIL"
            failed = [k for k, v in graded["checks"].items() if not v]
            print(f"{mark}  {key['id']:26s} trial {t}  tokens={usage.get('total_tokens', 0):,} "
                  f"tools={out['run'].get('tool_calls', 0)} athena={usage.get('athena_queries', 0)} "
                  f"{usage.get('seconds', 0)}s  {', '.join(failed)}")
            if out["run"]["status"] == "error":
                stopped = f"{key['id']} trial {t}: {out['run'].get('error')}"
                print(f"STOPPED: run error, remaining runs skipped. {stopped}")
                break
        passed = sum(t["passed"] for t in trials_out)
        results.append({"scenario": key["id"], "week": key["week"], "trials": trials,
                        "passed": passed, "pass_rate": round(passed / trials, 2),
                        "pass_at_k": passed > 0, "pass_all_k": passed == trials,
                        "runs": trials_out})
    provider = os.environ.get("WBR_MODEL_PROVIDER", "bedrock") if not model_factory else "scripted"
    all_runs = [t for r in results for t in r["runs"]]
    tin = sum(t["usage"].get("input_tokens", 0) for t in all_runs)
    tout = sum(t["usage"].get("output_tokens", 0) for t in all_runs)
    athena = backend.athena_usage()
    totals = {
        "runs": len(all_runs), "input_tokens": tin, "output_tokens": tout,
        "estimated_bedrock_usd": round(token_cost(tin, tout), 6),
        "model_calls": sum(t["usage"].get("model_calls", 0) for t in all_runs),
        "athena_attempts": athena["attempts"], "athena_bytes_scanned": athena["bytes_scanned"],
        "estimated_athena_usd": round(athena["billed_bytes_estimate"] / 1024 ** 4 * 5, 6),
        "seconds": round(sum(t["usage"].get("seconds", 0) for t in all_runs), 1),
        "stopped_early": stopped,
    }
    return {"provider": provider, "model": os.environ.get("WBR_MODEL_ID", "scripted"),
            "backend": config.BACKEND, "trials": trials, "totals": totals, "results": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--scenarios", nargs="*", default=[], help="e.g. S1 S2 (default: all)")
    parser.add_argument("--plan", action="store_true", help="print limits and worst-case cost; run nothing")
    args = parser.parse_args()

    if args.plan:
        print(json.dumps({"backend": config.BACKEND, "model": os.environ.get("WBR_MODEL_ID"),
                          **plan(args.trials, args.scenarios)}, indent=2))
        print("Plan only. Nothing was sent to AWS.")
        sys.exit(0)

    summary = evaluate(args.trials, args.scenarios)
    print(f"\n{'Scenario':28s} {'pass rate':>9s} {'pass@k':>7s} {'pass^k':>7s}")
    for r in summary["results"]:
        print(f"{r['scenario']:28s} {r['passed']}/{r['trials']:<7d} {str(r['pass_at_k']):>7s} {str(r['pass_all_k']):>7s}")
    total = sum(r["passed"] for r in summary["results"])
    count = sum(r["trials"] for r in summary["results"])
    print(f"\nOverall: {total}/{count} trials passed")
    t = summary["totals"]
    print(f"Tokens: {t['input_tokens']:,} in / {t['output_tokens']:,} out, "
          f"estimated Bedrock cost ${t['estimated_bedrock_usd']:.4f}")
    print(f"Athena: {t['athena_attempts']} attempts, {t['athena_bytes_scanned'] / 1024:.1f} KB scanned, "
          f"estimated ${t['estimated_athena_usd']:.6f}  |  run time {t['seconds']} s")
    if t["stopped_early"]:
        print(f"Stopped early: {t['stopped_early']}")

    runs = ROOT / "runs"
    runs.mkdir(exist_ok=True)
    path = runs / f"eval_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"Saved: {path}")