"""Evaluate the analyst agent on the golden scenarios.

    python scripts/evaluate_agent.py                      # all scenarios, 3 trials each
    python scripts/evaluate_agent.py --trials 5 --scenarios S1 S2

Each scenario runs several times because the model is not deterministic. Reports:
- pass rate:  share of trials that passed
- pass@k:     at least one of k trials passed (can it do it?)
- pass^k:     all k trials passed (does it do it reliably?)

Needs data/golden.duckdb, WBR_MODEL_ID, and AWS credentials for Bedrock.
Saves runs/eval_<time>.json with every report and, for failed trials, the transcript.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.analyst import run_analyst      # noqa: E402
from agent.evaluation import GRADERS, grade_run  # noqa: E402
from tools import config                    # noqa: E402


def evaluate(trials: int, only: list[str], model_factory=None) -> dict:
    config.DUCKDB_PATH = str(ROOT / "data" / "golden.duckdb")  # golden data on DuckDB
    if config.BACKEND == "athena":
        config.TABLE_PREFIX = "golden_"  # golden data on Athena: golden_weekly_metrics, ...
    keys = json.loads((ROOT / "tests" / "golden" / "answer_keys.json").read_text())
    scenarios = [s for s in keys["scenarios"] if s["id"] in GRADERS
                 and (not only or any(s["id"].startswith(o) for o in only))]
    results = []
    for key in scenarios:
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
            trials_out.append({"trial": t, **graded, "report_draft": out["report_draft"],
                               "transcript": None if graded["passed"] else out.get("transcript")})
            mark = "PASS" if graded["passed"] else "FAIL"
            failed = [k for k, v in graded["checks"].items() if not v]
            print(f"{mark}  {key['id']:26s} trial {t}  {', '.join(failed)}")
        passed = sum(t["passed"] for t in trials_out)
        results.append({"scenario": key["id"], "week": key["week"], "trials": trials,
                        "passed": passed, "pass_rate": round(passed / trials, 2),
                        "pass_at_k": passed > 0, "pass_all_k": passed == trials,
                        "runs": trials_out})
    provider = os.environ.get("WBR_MODEL_PROVIDER", "bedrock") if not model_factory else "scripted"
    return {"provider": provider, "model": os.environ.get("WBR_MODEL_ID", "scripted"),
            "trials": trials, "results": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--scenarios", nargs="*", default=[], help="e.g. S1 S2 (default: all)")
    args = parser.parse_args()

    summary = evaluate(args.trials, args.scenarios)
    print(f"\n{'Scenario':28s} {'pass rate':>9s} {'pass@k':>7s} {'pass^k':>7s}")
    for r in summary["results"]:
        print(f"{r['scenario']:28s} {r['passed']}/{r['trials']:<7d} {str(r['pass_at_k']):>7s} {str(r['pass_all_k']):>7s}")
    total = sum(r["passed"] for r in summary["results"])
    count = sum(r["trials"] for r in summary["results"])
    print(f"\nOverall: {total}/{count} trials passed")

    runs = ROOT / "runs"
    runs.mkdir(exist_ok=True)
    path = runs / f"eval_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"Saved: {path}")