"""Replay saved analyst runs through the shared bridge (no model, no Athena, no cost).

    python scripts/replay_captured.py runs/eval_YYYYMMDD_HHMMSS.json
    python scripts/replay_captured.py runs/eval_YYYYMMDD_HHMMSS.json --label PLANTED_REAL_OLIST

Prints each run's bridge status and verifier outcomes, and saves the renderer inputs
to runs/replay_<time>.json.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.replay import EXPECTED_TOOLS, replay  # noqa: E402
from src.wbr_contract import ContractError       # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("saved", help="a saved evaluation file (runs/eval_*.json)")
    parser.add_argument("--label", default="PRIVATE_CAPTURE_REPLAY")
    args = parser.parse_args()

    saved = json.loads(Path(args.saved).read_text())
    print(f"Source: {args.saved} (commit {saved.get('commit')})   expected tools: {', '.join(EXPECTED_TOOLS)}")
    out = []
    for result in saved["results"]:
        for trial in result["runs"]:
            try:
                res = replay(trial, result["week"], label=args.label)
            except ContractError as exc:   # rejected at the boundary (e.g. evidence week mismatch)
                print(f"\n{result['scenario']} week {result['week']}: REJECTED ({exc})")
                out.append({"scenario": result["scenario"], "week": result["week"],
                            "trial": trial.get("trial"), "status": "rejected", "reason": str(exc)})
                continue
            draft = trial.get("report_draft") or {}
            print(f"\n{result['scenario']} week {result['week']}: bridge {res.status} "
                  f"(analyst draft {draft.get('draft_status')}, unavailable {len(draft.get('unavailable', []))}, "
                  f"prechecks {len(draft.get('precheck_issues', []))})")
            for c in list(res.claims) + list(res.removed_claims):
                print(f"  {c['claim_id']:4s} {c['verifier_outcome']:7s} {c['verifier_reason']}")
            for w in res.warnings:
                if w.startswith(("missing expected", "unexpected", "analyst run failed")):
                    print(f"  ! {w}")
            out.append({"scenario": result["scenario"], "week": result["week"], "trial": trial.get("trial"),
                        "status": res.status, "renderer_input": res.renderer_input})
    path = ROOT / "runs" / f"replay_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"source": args.saved, "source_commit": saved.get("commit"),
                                "expected_tools": list(EXPECTED_TOOLS), "replays": out},
                               indent=2, default=str))
    print(f"\nSaved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
