"""Evaluate the analyst agent on the golden scenarios.

    python scripts/evaluate_agent.py --scenarios S1 S2 S3 S4 S5 S7 --trials 1          # plan only
    python scripts/evaluate_agent.py --scenarios S1 S2 S3 S4 S5 S7 --trials 1 --run    # the approved batch

Without --run nothing is sent anywhere: the script prints the exact scenarios, limits
and upper-bound cost. Scenarios run in the order given, so S1 is the smoke test, and
the batch stops at the first unexpected result.

Each scenario can run several times because the model is not deterministic. Reports:
- pass rate:  share of trials that passed
- pass@k:     at least one of k trials passed (can it do it?)
- pass^k:     all k trials passed (does it do it reliably?)

Guards (all enforced in code before a call is sent):
    per run:    25 tool calls, WBR_MAX_RUN_TOKENS cumulative tokens (default 300,000),
                WBR_MAX_OUTPUT_TOKENS per response (default 2,000), WBR_MAX_MODEL_CALLS
                attempts (default 30, SDK and Strands retries disabled). Each model call
                reserves the full context window (128,000) + output limit before sending.
                WBR_MAX_RUN_SECONDS is a SOFT investigation limit (then the report is
                written); hard limits are the Bedrock read and Athena query timeouts.
    per batch:  WBR_BATCH_BUDGET_USD dollar ledger (required with --run on AWS); Athena start
                attempts capped at WBR_MAX_ATHENA_QUERIES (default runs x 25 x 2);
                model attempts capped at runs x WBR_MAX_MODEL_CALLS
    stop:       at the first failed scenario, run error, access denial, uncertain execution,
                missing cost accounting, or cap risk. Nothing is retried blindly.

Saves runs/eval_<time>.json with, per run: verdict and checks, the report draft, the raw
evidence envelopes (request_id -> envelope), the tool-call list, model attempts with
reserved and actual tokens, AWS request IDs, Athena query receipts, times and costs.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.analyst import (CONTEXT_TOKENS, MAX_MODEL_CALLS, MAX_OUTPUT_TOKENS,  # noqa: E402
                           MAX_RUN_SECONDS, MAX_RUN_TOKENS, MAX_TOOL_CALLS, MODEL_READ_TIMEOUT,
                           run_analyst)
from agent.evaluation import GRADERS, grade_run  # noqa: E402
from tools import backend, config           # noqa: E402
from tools.budget import (LEDGER, PRICE_IN_PER_M, PRICE_OUT_PER_M, athena_reservation,  # noqa: E402
                          model_cost)


def short_id(scenario_id: str) -> str:
    return scenario_id.split("_")[0]          # "S1_category_drop" -> "S1"


def select(wanted: list[str]) -> list[dict]:
    """Exact scenario IDs, in the order given (S1 never matches S10)."""
    keys = json.loads((ROOT / "tests" / "golden" / "answer_keys.json").read_text())
    graded = {short_id(s["id"]): s for s in keys["scenarios"] if s["id"] in GRADERS}
    if not wanted:
        return list(graded.values())
    unknown = [w for w in wanted if w not in graded]
    if unknown:
        raise SystemExit(f"Unknown or ungraded scenario IDs: {unknown}. Graded: {sorted(graded)}")
    if len(set(wanted)) != len(wanted):
        raise SystemExit(f"Duplicate scenario IDs: {wanted}")
    return [graded[w] for w in wanted]


def limits(runs: int) -> dict:
    athena_cap = (config.MAX_ATHENA_QUERIES if config.MAX_ATHENA_QUERIES is not None
                  else runs * MAX_TOOL_CALLS * (config.MAX_RETRIES + 1))
    # Upper bounds. Tokens: the per-run cap is hard (reserved before each call), priced
    # at the dearer output rate. Athena: every attempt scanning the full 100 MiB cutoff.
    bedrock_max = runs * model_cost(0, MAX_RUN_TOKENS)
    athena_max = athena_cap * athena_reservation() if config.BACKEND == "athena" else 0.0
    return {
        "runs": runs,
        "per_run": {"tool_calls": MAX_TOOL_CALLS, "cumulative_tokens": MAX_RUN_TOKENS,
                    "output_tokens_per_response": MAX_OUTPUT_TOKENS,
                    "reserved_tokens_per_model_call": CONTEXT_TOKENS + MAX_OUTPUT_TOKENS,
                    "model_attempts": MAX_MODEL_CALLS, "sdk_retries": 0,
                    "soft_investigation_limit_s": MAX_RUN_SECONDS,
                    "bedrock_read_timeout_s": MODEL_READ_TIMEOUT,
                    "athena_query_timeout_s": config.QUERY_TIMEOUT_SECONDS},
        "batch": {"model_attempts": runs * MAX_MODEL_CALLS,
                  "athena_start_attempts": athena_cap if config.BACKEND == "athena" else 0,
                  "budget_ceiling_usd": LEDGER.ceiling_usd},
        "upper_bound_usd": {"bedrock": round(bedrock_max, 4), "athena_and_s3": round(athena_max, 4),
                            "total": round(bedrock_max + athena_max, 4)},
        "prices": {"input_per_million": PRICE_IN_PER_M, "output_per_million": PRICE_OUT_PER_M,
                   "athena_per_tb": 5.0, "athena_scan_cutoff": "100 MiB per query"},
    }


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=10).stdout.strip() or None
    except Exception:
        return None


def evaluate(trials: int, wanted: list[str], model_factory=None, stop_on_failure: bool = True,
             save_path: Path | None = None) -> dict:
    config.DUCKDB_PATH = str(ROOT / "data" / "golden.duckdb")  # golden data on DuckDB
    if config.BACKEND == "athena":
        config.TABLE_PREFIX = "golden_"  # golden data on Athena: golden_weekly_metrics, ...
    scenarios = select(wanted)
    runs_planned = len(scenarios) * trials
    lim = limits(runs_planned)
    if config.BACKEND == "athena":
        config.MAX_ATHENA_QUERIES = lim["batch"]["athena_start_attempts"]
    model_attempts_left = lim["batch"]["model_attempts"]

    results, stopped = [], None
    for key in scenarios:
        if stopped:
            break
        simulate = key.get("run_with", {}).get("WBR_SIMULATE_FAILURE", "")
        trials_out = []
        for t in range(1, trials + 1):
            if LEDGER.halted:
                stopped = f"ledger halted: {LEDGER.halted}"
                break
            config.SIMULATE_FAILURE = simulate
            try:
                model = model_factory(key, t) if model_factory else None
                out = run_analyst(key["week"], model=model,
                                  max_model_calls=min(MAX_MODEL_CALLS, model_attempts_left))
            finally:
                config.SIMULATE_FAILURE = ""
            usage = out["run"].get("usage", {})
            model_attempts_left -= usage.get("model_calls", 0)
            graded = grade_run(out, key)
            trials_out.append({
                "trial": t, **graded,
                "run_status": out["run"]["status"], "run_error": out["run"].get("error"),
                "usage": usage,
                "report_draft": out["report_draft"],
                "evidence_log": out.get("evidence_log"),   # raw envelopes the agent saw
                "calls": out.get("calls"),
                "transcript": out.get("transcript"),
            })
            if save_path:   # save evidence after every run, so nothing is lost if the batch stops
                partial = results + [{"scenario": key["id"], "week": key["week"], "runs": trials_out,
                                      "in_progress": True}]
                save_path.write_text(json.dumps(summarize(partial, scenarios, trials, runs_planned, lim,
                                                          "in progress", model_factory),
                                                indent=2, default=str))
            mark = "PASS" if graded["passed"] else "FAIL"
            failed = [k for k, v in graded["checks"].items() if not v]
            print(f"{mark}  {key['id']:26s} trial {t}  tokens={usage.get('total_tokens', 0):,} "
                  f"model={usage.get('model_calls', 0)} tools={out['run'].get('tool_calls', 0)} "
                  f"athena={usage.get('athena_queries', 0)} {usage.get('seconds', 0)}s  {', '.join(failed)}")
            if out["run"]["status"] == "error":
                stopped = f"{key['id']} trial {t}: run error: {out['run'].get('error')}"
            elif not graded["passed"] and stop_on_failure:
                stopped = f"{key['id']} trial {t}: unexpected scenario failure ({', '.join(failed)})"
            elif LEDGER.halted:
                stopped = f"{key['id']} trial {t}: ledger halted: {LEDGER.halted}"
            if stopped:
                print(f"STOPPED: {stopped}. Remaining runs skipped.")
                break
        passed = sum(t["passed"] for t in trials_out)
        results.append({"scenario": key["id"], "week": key["week"], "trials_run": len(trials_out),
                        "passed": passed,
                        "pass_rate": round(passed / len(trials_out), 2) if trials_out else None,
                        "pass_at_k": passed > 0, "pass_all_k": bool(trials_out) and passed == len(trials_out),
                        "runs": trials_out})

    return summarize(results, scenarios, trials, runs_planned, lim, stopped, model_factory)


def summarize(results, scenarios, trials, runs_planned, lim, stopped, model_factory=None) -> dict:
    all_runs = [t for r in results for t in r["runs"]]
    tin = sum(t["usage"].get("input_tokens", 0) for t in all_runs)
    tout = sum(t["usage"].get("output_tokens", 0) for t in all_runs)
    athena = backend.athena_usage()
    totals = {
        "runs": len(all_runs), "runs_planned": runs_planned,
        "input_tokens": tin, "output_tokens": tout,
        "estimated_bedrock_usd": round(model_cost(tin, tout), 6),
        "model_attempts": sum(t["usage"].get("model_calls", 0) for t in all_runs),
        "athena_start_attempts": athena["attempts"], "athena_bytes_scanned": athena["bytes_scanned"],
        "ledger": LEDGER.summary(),
        "seconds": round(sum(t["usage"].get("seconds", 0) for t in all_runs), 1),
        "stopped_early": stopped,
    }
    provider = os.environ.get("WBR_MODEL_PROVIDER", "bedrock") if not model_factory else "scripted"
    return {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "commit": git_commit(), "provider": provider,
            "model": os.environ.get("WBR_MODEL_ID", "scripted"), "region": config.AWS_REGION,
            "backend": config.BACKEND, "workgroup": config.ATHENA_WORKGROUP,
            "database": config.ATHENA_DATABASE, "scenarios": [s["id"] for s in scenarios],
            "trials": trials, "limits": lim, "totals": totals,
            "ledger_entries": LEDGER.entries, "athena_receipts": backend.QUERY_LOG,
            "results": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--scenarios", nargs="*", default=[], help="exact IDs in run order, e.g. S1 S2")
    parser.add_argument("--run", action="store_true", help="actually run (paid on AWS); default is plan only")
    args = parser.parse_args()

    scenarios = select(args.scenarios)
    print(f"Selected ({len(scenarios)} x {args.trials} trial(s) = {len(scenarios) * args.trials} runs): "
          + ", ".join(short_id(s["id"]) for s in scenarios))
    print(json.dumps({"backend": config.BACKEND, "model": os.environ.get("WBR_MODEL_ID"),
                      "region": config.AWS_REGION, "commit": git_commit(),
                      **limits(len(scenarios) * args.trials)}, indent=2))
    if not args.run:
        print("Plan only. Nothing was sent anywhere. Add --run for the approved batch.")
        sys.exit(0)
    paid = config.BACKEND == "athena" or os.environ.get("WBR_MODEL_PROVIDER", "bedrock") == "bedrock"
    if paid and not LEDGER.active:
        sys.exit("Refusing to run: set WBR_BATCH_BUDGET_USD (the approved dollar ceiling) first.")

    out_dir = ROOT / "runs"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"eval_{datetime.now():%Y%m%d_%H%M%S}.json"
    summary = evaluate(args.trials, args.scenarios, save_path=path)
    print(f"\n{'Scenario':28s} {'passed':>7s}")
    for r in summary["results"]:
        print(f"{r['scenario']:28s} {r['passed']}/{r['trials_run']}")
    t = summary["totals"]
    print(f"\nRuns: {t['runs']} of {t['runs_planned']} planned")
    print(f"Tokens: {t['input_tokens']:,} in / {t['output_tokens']:,} out, "
          f"{t['model_attempts']} model attempts, estimated Bedrock ${t['estimated_bedrock_usd']:.4f}")
    print(f"Athena: {t['athena_start_attempts']} start attempts, {t['athena_bytes_scanned'] / 1024:.1f} KB scanned")
    print(f"Ledger (upper estimate incl. Athena and S3): ${t['ledger']['spent_usd_upper_estimate']:.4f} "
          f"of ${t['ledger']['ceiling_usd']} ceiling; halted: {t['ledger']['halted']}")
    print(f"Run time: {t['seconds']} s")
    if t["stopped_early"]:
        print(f"Stopped early: {t['stopped_early']}")

    path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"Saved: {path}")
