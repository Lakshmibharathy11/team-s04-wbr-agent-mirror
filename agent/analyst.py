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
from strands.event_loop._retry import ModelRetryStrategy
from strands.tools.executors import SequentialToolExecutor

from agent.analyst_tools import make_tools
from agent.postchecks import finalize
from agent.prompts import FINAL_PROMPT, SYSTEM_PROMPT, investigation_prompt
from agent.run_context import RunContext
from agent.schema import DraftReport
from agent.steering import AnalystSteering
from tools import backend, config
from tools.envelope import now_iso
from tools.wbr_tools import ValidationError, _parse_week

MAX_TOOL_CALLS = 25   # budget of query-tool calls per run
MAX_TURNS = 40        # cap on model turns per invocation (Strands Limits)
# Cost guards (tokens = text the model reads + writes; Bedrock bills per token)
MAX_RUN_TOKENS = int(os.environ.get("WBR_MAX_RUN_TOKENS", "300000"))      # per run, all model calls
MAX_OUTPUT_TOKENS = int(os.environ.get("WBR_MAX_OUTPUT_TOKENS", "2000"))  # per model response
MAX_MODEL_CALLS = int(os.environ.get("WBR_MAX_MODEL_CALLS", "30"))         # model attempts per run
MAX_RUN_SECONDS = float(os.environ.get("WBR_MAX_RUN_SECONDS", "900"))     # SOFT limit: then write the report
CONTEXT_TOKENS = int(os.environ.get("WBR_CONTEXT_TOKENS", "128000"))     # model context window (Nova Micro)
MODEL_READ_TIMEOUT = 120   # seconds to wait for a model response


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
    from botocore.config import Config
    from strands.models import BedrockModel
    # total_max_attempts=1: no hidden SDK retries, so every model attempt is one we
    # reserved and counted (agent/steering.py). Failures stop the run instead.
    client_config = Config(retries={"total_max_attempts": 1}, connect_timeout=10,
                           read_timeout=MODEL_READ_TIMEOUT, user_agent_extra="strands-agents")
    return BedrockModel(model_id=model_id, region_name=config.AWS_REGION, temperature=0.0,
                        max_tokens=MAX_OUTPUT_TOKENS, boto_client_config=client_config)


def _attach_request_log(model, log: list) -> None:
    """Record every HTTP request the model client sends, with AWS's request ID.
    This is the independent count of model attempts and the receipt for each one."""
    client = getattr(model, "client", None)
    if client is None or not hasattr(client, "meta"):
        return   # e.g. the scripted test model or Ollama

    def on_send(request, **kwargs):
        log.append({"sent_at": now_iso(), "operation": kwargs.get("event_name", "").split(".")[-1],
                    "request_id": None, "http_status": None})

    def on_response(http_response, parsed, **kwargs):
        if log:
            meta = (parsed or {}).get("ResponseMetadata", {})
            log[-1].update(request_id=meta.get("RequestId"), http_status=meta.get("HTTPStatusCode"))

    client.meta.events.register("before-send.bedrock-runtime", on_send)
    client.meta.events.register("after-call.bedrock-runtime", on_response)


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
        "guard_counted_tokens": steering.used_tokens,
        "token_cap": steering.max_tokens,
        "token_cap_hit": steering.token_cap_hit,
        "wrap_up": steering.wrap_up,
        "reserved_tokens_per_call": steering.context_tokens + steering.max_output_tokens,
        "model_calls": steering.model_calls,
        "model_call_limit": steering.max_model_calls,
        "model_calls_cancelled": steering.model_calls_cancelled,
        "model_attempts": steering.model_log,          # reservation vs actual, per attempt
        "http_requests": steering.http_log,            # AWS request IDs (Bedrock only)
        "stop_reason": steering.stop_reason,
        "athena_queries": len(athena),
        "athena_bytes_scanned": sum(q.get("bytes_scanned") or 0 for q in athena),
        "athena_receipts": athena,                     # query IDs, state, bytes, times
        "seconds": round(time.monotonic() - started, 1),
    }


def run_analyst(week: str, model=None, max_tool_calls: int = MAX_TOOL_CALLS,
                max_turns: int = MAX_TURNS, max_tokens: int | None = MAX_RUN_TOKENS,
                max_model_calls: int | None = MAX_MODEL_CALLS,
                max_seconds: float | None = MAX_RUN_SECONDS,
                strict_accounting: bool | None = None,
                context_tokens: int = CONTEXT_TOKENS) -> dict:
    started = time.monotonic()
    athena_before = len(backend.QUERY_LOG)
    try:
        week_date = _parse_week(week)
    except ValidationError as exc:
        return {"run": {"status": "error", "error": str(exc)}, "report_draft": None,
                "evidence_log": {}, "transcript": []}

    ctx = RunContext(week_date.isoformat(), max_tool_calls=max_tool_calls)
    model = model or default_model()
    paid = type(model).__name__ == "BedrockModel" if strict_accounting is None else strict_accounting
    steering = AnalystSteering(ctx, max_tokens=max_tokens, max_output_tokens=MAX_OUTPUT_TOKENS,
                               max_model_calls=max_model_calls, max_seconds=max_seconds,
                               strict_accounting=paid, context_tokens=context_tokens)
    steering.http_log = []
    _attach_request_log(model, steering.http_log)
    # max_attempts=1: Strands does not retry failed model calls on its own
    agent = Agent(model=model, tools=make_tools(ctx), system_prompt=SYSTEM_PROMPT,
                  hooks=[steering], callback_handler=None,
                  retry_strategy=ModelRetryStrategy(max_attempts=1),
                  # One tool at a time, so the tool budget and Athena caps are checked
                  # before every single query (the default runs tools in parallel)
                  tool_executor=SequentialToolExecutor())

    try:
        result = agent(investigation_prompt(ctx.week), structured_output_model=DraftReport,
                       limits={"turns": max_turns})
        draft = result.structured_output
        if draft is None and not steering.stop_reason:
            # Turn cap reached before the report: block further tools and ask for the report
            ctx.max_tool_calls = ctx.tool_calls_used
            result = agent(FINAL_PROMPT, structured_output_model=DraftReport, limits={"turns": 5})
            draft = result.structured_output
        if steering.stop_reason:
            raise RuntimeError(f"Run stopped by guard: {steering.stop_reason}")
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
