"""Run the analyst agent for one week and save the result.

    python scripts/run_analyst.py --week 2018-06-04
    python scripts/run_analyst.py --week <scenario week> --golden
    WBR_SIMULATE_FAILURE=avg_review_score python scripts/run_analyst.py --week 2018-07-02 --golden

Needs WBR_MODEL_ID (a Bedrock model enabled in the account) and AWS credentials.
Saves runs/<week>_<time>.json with the report draft, evidence log, and transcript.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.analyst import run_analyst  # noqa: E402
from tools import config               # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--week", required=True, help="Monday of the week, e.g. 2018-06-04")
parser.add_argument("--golden", action="store_true", help="use data/golden.duckdb (planted test scenarios)")
args = parser.parse_args()

if args.golden:
    config.DUCKDB_PATH = str(ROOT / "data" / "golden.duckdb")
    if config.BACKEND == "athena":
        config.TABLE_PREFIX = "golden_"  # golden tables on Athena

out = run_analyst(args.week)
runs = ROOT / "runs"
runs.mkdir(exist_ok=True)
path = runs / f"{args.week}_{datetime.now():%Y%m%d_%H%M%S}.json"
path.write_text(json.dumps(out, indent=2, default=str))

print(f"Run status: {out['run']['status']}  ({out['run'].get('tool_calls', 0)} tool calls)")
u = out["run"].get("usage", {})
if u:
    print(f"Tokens: {u['input_tokens']:,} in / {u['output_tokens']:,} out ({u['model_calls']} model calls"
          f"{', token cap hit' if u['token_cap_hit'] else ''})  |  Athena: {u['athena_queries']} queries, "
          f"{u['athena_bytes_scanned'] / 1024:.1f} KB  |  {u['seconds']} s")
if out["report_draft"]:
    report = out["report_draft"]
    print(f"Draft status: {report['draft_status']}\n")
    for c in report["claims"]:
        print(f"[{c['claim_id']} {c['question_id']} {c['claim_type']} {c['confidence']}] {c['statement']}")
    for q in report["actionable_questions"]:
        print(f"? {q}")
    for issue in report["precheck_issues"]:
        print(f"! {issue}")
else:
    print(out["run"].get("error"))
print(f"\nSaved: {path}")