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
from tools import backend, config
from tools.wbr_tools import ValidationError, _parse_week

MAX_TOOL_CALLS = 25   # budget of query-tool calls per run
MAX_TURNS = 40        # cap on model turns per invocation (Strands Limits)
# Cost guards (tokens = text the model reads + writes; Bedrock bills per token)
MAX_RUN_TOKENS = int(os.environ.get("WBR_MAX_RUN_TOKENS", "300000"))      # per run, all model calls
MAX_OUTPUT_TOKENS = int(os.environ.get("WBR_MAX_OUTPUT_TOKENS", "2000"))  # per model response


def default_model():
    """The model from settings.

    WBR_MODEL_PROVIDER: "bedrock" (default) or "ollama" (a free local model, for testing)
    WBR_MODEL_ID:       the Bedrock model ID, or the Ollama model name (e.g. qwen2.5:7b)
    WBR_OLLAMA_HOST:    Ollama address (default http://localhost:11434)
    """
    provider = os.environ.get("WBR_MODEL_PROVIDER", "bedrock").lower()
    model_id = os.environ.get("WBR_MODEL_ID")
    if not model_id:
        raise RuntimeError("Set WBR_MODEL_ID (a Bedrock model ID, or an Ollama model name)")
    if provider == "ollama":
        from strands.models.ollama import OllamaModel
        host = os.environ.get("WBR_OLLAMA_HOST", "http://localhost:11434")
        return OllamaModel(host, model_id=model_id, temperature=0.0)
    from strands.models import BedrockModel
    return BedrockModel(model_id=model_id, region_name=config.AWS_REGION, temperature=0.0,
                        max_tokens=MAX_OUTPUT_TOKENS)


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


def _usage(agent, steering, athena_before: int, started: float) -> dict:
    """What this run consumed: tokens, model calls, Athena queries, time."""
    tokens = dict(agent.event_loop_metrics.accumulated_usage)
    athena = backend.QUERY_LOG[athena_before:]
    return {
        "input_tokens": tokens.get("inputTokens", 0),
        "output_tokens": tokens.get("outputTokens", 0),
        "total_tokens": tokens.get("totalTokens", 0),
        "token_cap": steering.max_tokens,
        "token_cap_hit": steering.token_cap_hit,
        "model_calls": steering.model_calls,
        "model_calls_cancelled": steering.model_calls_cancelled,
        "athena_queries": len(athena),
        "athena_bytes_scanned": sum(q.get("bytes_scanned") or 0 for q in athena),
        "seconds": round(time.monotonic() - started, 1),
    }


def run_analyst(week: str, model=None, max_tool_calls: int = MAX_TOOL_CALLS,
                max_turns: int = MAX_TURNS, max_tokens: int | None = MAX_RUN_TOKENS) -> dict:
    started = time.monotonic()
    athena_before = len(backend.QUERY_LOG)
    try:
        week_date = _parse_week(week)
    except ValidationError as exc:
        return {"run": {"status": "error", "error": str(exc)}, "report_draft": None,
                "evidence_log": {}, "transcript": []}

    ctx = RunContext(week_date.isoformat(), max_tool_calls=max_tool_calls)
    steering = AnalystSteering(ctx, max_tokens=max_tokens)
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
                        "tool_calls": ctx.tool_calls_used,
                        "usage": _usage(agent, steering, athena_before, started)},
                "report_draft": None, "evidence_log": ctx.evidence_log,
                "transcript": _transcript(agent.messages)}

    report = finalize(draft, ctx)
    report = {"week": ctx.week, "compare_week": (week_date - timedelta(days=7)).isoformat(), **report}
    return {
        "run": {"status": "ok", "tool_calls": ctx.tool_calls_used, "budget": max_tool_calls,
                "blocked_calls": steering.blocked, "seconds": round(time.monotonic() - started, 1),
                "backend": config.BACKEND,
                "usage": _usage(agent, steering, athena_before, started)},
        "report_draft": report,
        "evidence_log": ctx.evidence_log,
        "transcript": _transcript(agent.messages),
        "calls": ctx.calls,
    }