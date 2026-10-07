"""The analyst agent loop.

    run_analyst("2018-06-04") -> {"report_draft": ..., "evidence_log": ..., "transcript": ..., "run": ...}

The model decides which tool to call next; the tools calculate; the run context
records every result; steering hooks enforce the budget and block repeats; code
adds confidence and pre-checks after the model writes the draft.
"""

import os
import time
from datetime import timedelta

from strands import Agent

from agent.analyst_tools import make_tools
from agent.postchecks import finalize
from agent.prompts import FINAL_PROMPT, SYSTEM_PROMPT, investigation_prompt
from agent.run_context import RunContext
from agent.schema import DraftReport
from agent.steering import AnalystSteering
from tools import config
from tools.wbr_tools import ValidationError, _parse_week

MAX_TOOL_CALLS = 25   # budget of query-tool calls per run
MAX_TURNS = 40        # cap on model turns per invocation (Strands Limits)


def default_model():
    """Bedrock model from settings. WBR_MODEL_ID must name a model enabled in the account."""
    from strands.models import BedrockModel
    model_id = os.environ.get("WBR_MODEL_ID")
    if not model_id:
        raise RuntimeError("Set WBR_MODEL_ID to a Bedrock model ID enabled in your account")
    return BedrockModel(model_id=model_id, region_name=config.AWS_REGION, temperature=0.0)


def _transcript(messages: list) -> list[dict]:
    """The model's steps in order: text, tool calls, and tool results (by request_id)."""
    steps = []
    for msg in messages:
        for block in msg.get("content", []):
            if "text" in block and block["text"].strip():
                steps.append({"role": msg["role"], "text": block["text"]})
            elif "toolUse" in block:
                steps.append({"role": "assistant", "tool_call": block["toolUse"]["name"],
                              "input": block["toolUse"].get("input")})
            elif "toolResult" in block:
                result = block["toolResult"]
                steps.append({"role": "tool", "status": result.get("status"),
                              "content": result.get("content")})
    return steps


def run_analyst(week: str, model=None, max_tool_calls: int = MAX_TOOL_CALLS,
                max_turns: int = MAX_TURNS) -> dict:
    started = time.monotonic()
    try:
        week_date = _parse_week(week)
    except ValidationError as exc:
        return {"run": {"status": "error", "error": str(exc)}, "report_draft": None,
                "evidence_log": {}, "transcript": []}

    ctx = RunContext(week_date.isoformat(), max_tool_calls=max_tool_calls)
    steering = AnalystSteering(ctx)
    agent = Agent(model=model or default_model(), tools=make_tools(ctx),
                  system_prompt=SYSTEM_PROMPT, hooks=[steering], callback_handler=None)

    try:
        result = agent(investigation_prompt(ctx.week), structured_output_model=DraftReport,
                       limits={"turns": max_turns})
        draft = result.structured_output
        if draft is None:
            # Turn cap reached before the report: block further tools and ask for the report
            ctx.max_tool_calls = ctx.tool_calls_used
            result = agent(FINAL_PROMPT, structured_output_model=DraftReport, limits={"turns": 5})
            draft = result.structured_output
        if draft is None:
            raise RuntimeError("The model did not return a report")
    except Exception as exc:   # model or network failure: report it, never crash the runner
        return {"run": {"status": "error", "error": f"{type(exc).__name__}: {exc}",
                        "tool_calls": ctx.tool_calls_used},
                "report_draft": None, "evidence_log": ctx.evidence_log,
                "transcript": _transcript(agent.messages)}

    report = finalize(draft, ctx)
    report = {"week": ctx.week, "compare_week": (week_date - timedelta(days=7)).isoformat(), **report}
    return {
        "run": {"status": "ok", "tool_calls": ctx.tool_calls_used, "budget": max_tool_calls,
                "blocked_calls": steering.blocked, "seconds": round(time.monotonic() - started, 1),
                "backend": config.BACKEND},
        "report_draft": report,
        "evidence_log": ctx.evidence_log,
        "transcript": _transcript(agent.messages),
        "calls": ctx.calls,
    }
