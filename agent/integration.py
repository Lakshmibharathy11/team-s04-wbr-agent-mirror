"""Adapter: one analyst run -> the runner contract in src/wbr_runner.py (Franklyn's side).

Nothing here changes the analyst, the tools, or the runner; it only converts formats.

Differences it bridges:
- evidence_refs: the runner wants strings, and indexes evidence by them. Each result's
  evidence ref becomes its request_id, which is exactly what claims cite. The full
  source details (query_id, table, Athena execution id) stay in evidence_details.
- warnings: the runner wants strings -> "CODE: message"; full objects in warning_details.
- error: the runner wants a truthy value -> "type: message"; full object in error_details.
- one name per tool call: the runner rejects repeated tool names, but the agent calls
  get_breakdown several times. Each recorded call gets a unique, readable name
  (e.g. "get_breakdown:product_revenue:by_state:category=health_beauty"); the original
  name stays in source_tool_name.
- no new queries: the runner calls each tool and retries failures once. Here every
  "tool" replays the recorded envelope, so a retry repeats nothing on Athena.
- every recorded call is passed on, failed ones included (nothing is left out).

OPEN (reviewer findings, not fixed here yet; do not treat this adapter as final):
- report-level limits (draft_status PARTIAL, unavailable, precheck_issues, run status)
  are not passed into run_wbr;
- expected_tool_names is built from the calls the agent made, so a skipped call can
  never show as missing; it must come from an independent expected plan;
- renamed tool names can bypass a selector that looks results up by the original
  name; the original envelope should be kept (source_envelope) or matching tested.
"""

def call_label(call: dict) -> str:
    args = call["args"]
    parts = [call["tool"]]
    if args.get("metric"):
        parts.append(args["metric"])
    if args.get("dimension"):
        parts.append(f"by_{args['dimension']}")
    parts += [f"{k}={args[k]}" for k in ("state", "category") if args.get(k)]
    return ":".join(parts)


def to_runner_envelope(env: dict, name: str) -> dict:
    ok = env["status"] in ("ok", "partial")
    error = env.get("error")
    out = dict(env)
    out.update({
        "tool_name": name,
        "source_tool_name": env["tool_name"],
        "evidence_refs": [env["request_id"]] if ok else [],
        "evidence_details": env.get("evidence_refs", []),
        "warnings": [f"{w['code']}: {w['message']}" for w in env.get("warnings", [])],
        "warning_details": env.get("warnings", []),
        "error": f"{error.get('type')}: {error.get('message')}" if isinstance(error, dict) else error,
        "error_details": error,
    })
    return out


def runner_inputs(run: dict):
    """From a run_analyst() result (or a saved run with report_draft, evidence_log, calls):
    returns (tool_calls, analyst, expected_tool_names, excluded_calls) for run_wbr()."""
    if not run.get("report_draft"):
        raise ValueError("run has no report_draft (the run did not finish)")
    evidence, calls = run["evidence_log"], run["calls"]
    tool_calls, names, excluded = [], [], []   # excluded stays empty: nothing is dropped
    for call in calls:
        name = base = call_label(call)
        n = 2
        while name in names:          # only if two calls share a label
            name, n = f"{base}#{n}", n + 1
        names.append(name)
        payload = to_runner_envelope(evidence[call["request_id"]], name)
        tool_calls.append((name, lambda _params, payload=payload: payload, dict(call["args"])))

    claims = [dict(c) for c in run["report_draft"]["claims"]]

    def analyst(_tool_results):
        return claims

    return tool_calls, analyst, tuple(names), excluded


def acceptance_fixture(run: dict, label: str, answer_key: dict | None = None) -> dict:
    """A fixture for demo/run_real_olist_acceptance.py. The answer key must come from
    the verifier owner, not from this run (a key built from the run would grade itself)."""
    tool_calls, analyst, names, excluded = runner_inputs(run)
    return {
        "label": label,
        "expected_tool_names": list(names),
        "tool_results": [call(params) for _, call, params in tool_calls],
        "claims": analyst(()),
        "answer_key": answer_key or {},
        "analyst_report": {k: v for k, v in run["report_draft"].items() if k != "claims"},
        "excluded_calls": excluded,
    }
