"""Step 7: check that the three tools return the same results on Athena as on DuckDB.

Each case calls one tool twice: once on DuckDB (local, free) and once on Athena
(paid, tiny). The two envelopes are compared field by field, ignoring only the
fields that are supposed to differ (request id, timestamps, backend, table name).

Safety:
  - Without --run, nothing is sent to AWS: it only prints the plan and the cost cap.
  - A hard cap (WBR_MAX_ATHENA_QUERIES) stops the script from sending more
    Athena queries than planned, retries included.

Usage (project root, venv active):
    python scripts/athena_parity.py                # plan only, no AWS calls
    $env:AWS_PROFILE="s04-sdk"                    # PowerShell; then:
    python scripts/athena_parity.py --run          # the approved run
"""

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import backend, config  # noqa: E402
from tools.wbr_tools import get_breakdown, get_delivery_signals, get_weekly_metric  # noqa: E402

TOOLS = {
    "get_weekly_metric": get_weekly_metric,
    "get_breakdown": get_breakdown,
    "get_delivery_signals": get_delivery_signals,
}
DUCKDB_FILES = {"source": ROOT / "data" / "olist.duckdb", "golden": ROOT / "data" / "golden.duckdb"}
ATHENA_PREFIX = {"source": "", "golden": "golden_"}
MB = 1024 * 1024
PRICE_PER_TB = 5.0


def golden_weeks() -> dict:
    """Weeks chosen for S1-S3 on your data, read from the answer keys."""
    keys = json.loads((ROOT / "tests" / "golden" / "answer_keys.json").read_text())
    return {s["id"].split("_")[0]: s["week"] for s in keys["scenarios"]}


def build_cases() -> list[dict]:
    w = golden_weeks()
    return [
        # Real data: one call per tool on a week with a real movement
        {"id": "R1", "data": "source", "tool": "get_weekly_metric", "args": {"metric": "product_revenue", "week": "2018-03-12"}},
        {"id": "R2", "data": "source", "tool": "get_breakdown", "args": {"metric": "product_revenue", "week": "2018-03-12", "dimension": "category"}},
        {"id": "R3", "data": "source", "tool": "get_delivery_signals", "args": {"week": "2018-03-12"}},
        # Golden scenarios: happy path and failure paths
        {"id": "S1", "data": "golden", "tool": "get_breakdown", "args": {"metric": "product_revenue", "week": w["S1"], "dimension": "category"}},
        {"id": "S2", "data": "golden", "tool": "get_delivery_signals", "args": {"week": w["S2"], "state": "RJ"}},
        {"id": "S3", "data": "golden", "tool": "get_weekly_metric", "args": {"metric": "product_revenue", "week": w["S3"], "state": "MG"}},
        {"id": "S4", "data": "golden", "tool": "get_weekly_metric", "args": {"metric": "product_revenue", "week": "2018-08-20"}},
        {"id": "S5", "data": "golden", "tool": "get_weekly_metric", "args": {"metric": "avg_review_score", "week": "2018-07-02"},
         "simulate": "avg_review_score", "athena_queries": 0},
        {"id": "S6", "data": "golden", "tool": "get_delivery_signals", "args": {"week": "2018-01-15", "state": "PR"}},
        {"id": "S7", "data": "golden", "tool": "get_weekly_metric", "args": {"metric": "product_revenue", "week": "2018-04-23"}},
        {"id": "S8", "data": "golden", "tool": "get_weekly_metric", "args": {"metric": "late_delivery_pct", "week": "2018-02-19", "state": "SC"}},
        {"id": "S9", "data": "golden", "tool": "get_weekly_metric", "args": {"metric": "net_profit", "week": "2018-03-12"},
         "athena_queries": 0},
        {"id": "S10", "data": "golden", "tool": "get_weekly_metric", "args": {"metric": "product_revenue", "week": "2017-02-13"}},
    ]


def call(case: dict, backend_name: str) -> dict:
    """Run one tool call on one backend."""
    config.BACKEND = backend_name
    config.DUCKDB_PATH = str(DUCKDB_FILES[case["data"]])
    config.TABLE_PREFIX = ATHENA_PREFIX[case["data"]] if backend_name == "athena" else ""
    config.SIMULATE_FAILURE = case.get("simulate", "")
    try:
        return TOOLS[case["tool"]](**case["args"])
    finally:
        config.SIMULATE_FAILURE = ""


def comparable(env: dict) -> dict:
    """The envelope minus the fields that are meant to differ between backends."""
    return {
        "status": env["status"],
        "data": env["data"],
        "warnings": env["warnings"],
        "error_type": (env["error"] or {}).get("type"),
        "row_counts": [r["row_count"] for r in env["evidence_refs"]],
    }


def diff(a, b, path="") -> list[str]:
    """Differences between two values; numbers equal within a tiny tolerance."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            out += diff(a.get(k, "<missing>"), b.get(k, "<missing>"), f"{path}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} vs {len(b)}"]
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in diff(x, y, f"{path}[{i}]")]
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return [] if math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9) else [f"{path}: {a} vs {b}"]
    return [] if a == b else [f"{path}: {a!r} vs {b!r}"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="actually send the Athena queries")
    args = parser.parse_args()

    cases = build_cases()
    planned = sum(c.get("athena_queries", 1) for c in cases)
    cap = planned * (config.MAX_RETRIES + 1)
    worst_cost = cap * 10 * MB / 1024 ** 4 * PRICE_PER_TB

    print(f"Cases: {len(cases)}  |  Athena queries planned: {planned}  |  "
          f"hard cap incl. retries: {cap}  |  worst-case cost: ${worst_cost:.4f}")
    print(f"Workgroup {config.ATHENA_WORKGROUP}, database {config.ATHENA_DATABASE}, region {config.AWS_REGION}\n")
    for c in cases:
        print(f"  {c['id']:4s} {c['data']:6s} {c['tool']:22s} {c['args']}"
              + (f"  simulate={c['simulate']}" if c.get("simulate") else ""))
    if not args.run:
        print("\nPlan only. Nothing was sent to AWS. Add --run for the approved run.")
        return 0

    config.MAX_ATHENA_QUERIES = cap
    results, started = [], time.monotonic()
    print()
    for c in cases:
        before = len(backend.QUERY_LOG)
        local = call(c, "duckdb")
        athena = call(c, "athena")
        sent = backend.QUERY_LOG[before:]
        problems = diff(comparable(local), comparable(athena))
        mark = "MATCH" if not problems else "DIFF "
        print(f"{mark} {c['id']:4s} status duckdb={local['status']:7s} athena={athena['status']:7s} "
              f"athena attempts={len(sent)}")
        for p in problems[:5]:
            print(f"        {p}")
        results.append({"case": c, "match": not problems, "differences": problems,
                        "duckdb": comparable(local), "athena": comparable(athena),
                        "athena_queries": sent})

    usage = backend.athena_usage()
    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cases": len(cases), "matched": sum(r["match"] for r in results),
        "athena_attempts": usage["attempts"], "athena_succeeded": usage["succeeded"],
        "bytes_scanned": usage["bytes_scanned"],
        "estimated_cost_usd": round(usage["billed_bytes_estimate"] / 1024 ** 4 * PRICE_PER_TB, 6),
        "athena_engine_ms": usage["engine_ms"],
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "cap": cap,
    }
    out_dir = ROOT / "runs"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"athena_parity_{datetime.now():%Y%m%d_%H%M%S}.json"
    out_file.write_text(json.dumps({"summary": summary, "results": results}, indent=2, default=str))

    print(f"\n{summary['matched']}/{summary['cases']} cases match")
    print(f"Athena attempts: {summary['athena_attempts']} (cap {cap}), "
          f"data scanned: {summary['bytes_scanned'] / 1024:.1f} KB, "
          f"estimated cost: ${summary['estimated_cost_usd']:.6f}")
    print(f"Athena engine time: {summary['athena_engine_ms']} ms, total elapsed: {summary['elapsed_seconds']} s")
    print(f"Saved: {out_file}")
    return 0 if summary["matched"] == summary["cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
