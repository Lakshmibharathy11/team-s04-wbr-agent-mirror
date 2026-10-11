"""Integration caller: replay a captured analyst run through the shared bridge.

    run_analyst(...) or a saved evaluation run  ->  capture()  ->  replay()
        -> agent.runner_bridge.run_captured  ->  src.analyst_adapter.run_analyst_week
        -> runner verification (numeric verifier) -> renderer input

Replay only: nothing here calls the model, Athena or any data tool. The recorded
tool envelopes are passed through unchanged; src/ keeps each original capture
(source_envelope) for numeric verification, and decides the final status.

Ownership: this caller is the analyst side's. The bridge, runner, verifier and
acceptance policy in src/ are Franklyn's and are used as they are.
"""

from copy import deepcopy

from agent.postchecks import wrong_week
from agent.runner_bridge import run_captured
from src.wbr_contract import ContractError

# Engineering contract: the tools every analyst run is expected to cover. Fixed here,
# never derived from the calls a run made, so an omitted tool shows as missing.
# (Proposed by the analyst owner; to be confirmed by the acceptance owner.)
EXPECTED_TOOLS = ("get_weekly_metric", "get_breakdown", "get_delivery_signals")


def capture(run: dict) -> dict:
    """The captured-run shape the bridge requires, from either source:
    - run_analyst() output: has run = {"status": ..., ...}
    - a saved evaluation run (scripts/evaluate_agent.py): has run_status / run_error
    """
    if isinstance(run.get("run"), dict):
        meta = deepcopy(run["run"])
    else:
        meta = {"status": run.get("run_status"), "error": run.get("run_error")}
    return {
        "run": meta,
        "report_draft": deepcopy(run.get("report_draft")),
        "evidence_log": deepcopy(run.get("evidence_log") or {}),
        "calls": deepcopy(run.get("calls") or []),
        "transcript": deepcopy(run.get("transcript") or []),
    }


def check_weeks(captured: dict, week: str) -> None:
    """Reject a capture whose evidence is not for the replay week. A result's own
    previous week and baseline are part of that result, so they are valid; a result
    for a different report week (e.g. 2018-08-20 in a 2018-08-13 report) is not."""
    off = sorted(rid for rid, env in captured["evidence_log"].items() if wrong_week(env, week))
    if off:
        raise ContractError(f"evidence week mismatch: {len(off)} result(s) not for week {week}: "
                            + ", ".join(off))


def replay(run: dict, week: str, *, expected_tool_names=EXPECTED_TOOLS, verifier=None,
           renderer=None, label: str = "PRIVATE_CAPTURE_REPLAY"):
    """Replay one captured run through the bridge. Returns the runner's RunResult.
    Raises ContractError if any evidence is for a different week."""
    captured = capture(run)
    check_weeks(captured, week)
    return run_captured(captured, week=week, expected_tool_names=expected_tool_names,
                        verifier=verifier, renderer=renderer, label=label)
