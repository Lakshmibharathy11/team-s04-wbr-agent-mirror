"""Steering hooks: just-in-time guidance and hard guards at the agent's decision points.

Guidance (instead of a longer prompt):
- the tool budget is used up      -> stop investigating and write the report
- an identical call is repeated   -> point to the earlier result instead

Guards (cost and safety), checked BEFORE every model call:
- reservation: every call reserves the FULL model context window (the most input the
               model can accept; longer requests are rejected by Bedrock) plus the
               output limit. This is a hard upper bound that needs no token estimate,
               so the run's token cap and the batch dollar ledger cannot be overshot.
- wrap-up:     investigation continues only while a further call AND a final report
               call would both fit; otherwise tools are blocked and the next call
               writes the report. A call that cannot fit is refused, not sent.
- attempts:    a finite number of model attempts per run (SDK and Strands retries are
               disabled in analyst.py, so every attempt is one counted here)
- soft time limit: after a set time, investigation stops and the report is written.
               This is NOT a hard deadline; hard limits are the per-request timeouts
               (Bedrock read timeout, Athena query timeout).
- accounting:  a call that fails (uncertain execution), returns no usage numbers, or
               reports more usage than was reserved, stops the run and halts the batch
"""

import time

from strands.hooks import (AfterModelCallEvent, BeforeModelCallEvent, BeforeToolCallEvent,
                           HookProvider, HookRegistry)

from agent.run_context import RunContext
from tools.budget import LEDGER, BudgetError, model_cost
from tools.envelope import now_iso

QUERY_TOOLS = {"get_weekly_metric", "get_breakdown", "get_delivery_signals"}

DEFAULT_CONTEXT_TOKENS = 128_000   # Amazon Nova Micro context window


class AnalystSteering(HookProvider):
    def __init__(self, ctx: RunContext, max_tokens: int | None = None, max_output_tokens: int = 2000,
                 max_model_calls: int | None = None, max_seconds: float | None = None,
                 strict_accounting: bool = False, context_tokens: int = DEFAULT_CONTEXT_TOKENS):
        self.ctx = ctx
        self.max_tokens = max_tokens              # cumulative input+output tokens per run
        self.max_output_tokens = max_output_tokens
        self.max_model_calls = max_model_calls    # finite model attempts per run
        self.max_seconds = max_seconds            # SOFT investigation time limit (see header)
        self.context_tokens = context_tokens      # hard bound on any one call's input
        self.strict = strict_accounting           # True for paid models: usage numbers required
        self.started = time.monotonic()

        self.blocked: list[dict] = []             # tool calls blocked (for the transcript)
        self.model_log: list[dict] = []           # every model attempt: reservation and actual usage
        self.used_tokens = 0
        self.model_calls = 0                      # attempts actually sent
        self.model_calls_cancelled = 0            # attempts refused before sending
        self.wrap_up = False                      # investigation stopped; next call writes the report
        self.token_cap_hit = False
        self.stop_reason: str | None = None       # set when the run must end
        self._pending: dict | None = None

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(BeforeModelCallEvent, self.before_model)
        registry.add_callback(AfterModelCallEvent, self.after_model)

    # ------------------------------------------------------------------ model guards
    def _cancel(self, event, message: str, stop: str | None = None) -> None:
        event.cancel = message
        self.model_calls_cancelled += 1
        if stop and not self.stop_reason:
            self.stop_reason = stop

    def _start_wrap_up(self, event, why: str) -> None:
        self.wrap_up = True
        self.ctx.max_tool_calls = self.ctx.tool_calls_used   # block further tools
        self._cancel(event, f"{why} Write the report now with the evidence you already have.")

    def before_model(self, event: BeforeModelCallEvent) -> None:
        if self.stop_reason:
            self._cancel(event, f"Run stopped: {self.stop_reason}")
            return
        if LEDGER.halted:
            self._cancel(event, "Batch halted.", stop=f"batch halted: {LEDGER.halted}")
            return
        if self.max_model_calls is not None and self.model_calls >= self.max_model_calls:
            self._cancel(event, "Model attempt limit reached.",
                         stop=f"model attempt limit ({self.max_model_calls}) reached")
            return

        # Hard upper bound for this call: full context window + output limit
        reserve = self.context_tokens + self.max_output_tokens

        if not self.wrap_up:
            if self.max_seconds is not None and time.monotonic() - self.started >= self.max_seconds:
                self._start_wrap_up(event, f"Soft time limit ({self.max_seconds:.0f} s) reached.")
                return
            # Keep investigating only if this call AND a final report call would both fit
            if self.max_tokens is not None and self.used_tokens + 2 * reserve > self.max_tokens:
                self.token_cap_hit = True
                self._start_wrap_up(event, f"Token budget: {self.used_tokens:,} used of {self.max_tokens:,}; "
                                           f"another investigation call plus the report call would reserve "
                                           f"{2 * reserve:,} tokens (worst case), which would not fit.")
                return
        if self.max_tokens is not None and self.used_tokens + reserve > self.max_tokens:
            self.token_cap_hit = True
            self._cancel(event, "Token budget exhausted.", stop="token cap: the next call could not fit")
            return

        try:
            rid = LEDGER.reserve("bedrock", model_cost(self.context_tokens, self.max_output_tokens),
                                 f"run {self.ctx.week} call {self.model_calls + 1}")
        except BudgetError as exc:
            self._cancel(event, "Batch budget reached.", stop=str(exc))
            return
        self.model_calls += 1
        self._pending = {"attempt": self.model_calls, "sent_at": now_iso(), "ledger_id": rid,
                         "reserved_input_tokens": self.context_tokens,
                         "projected_input_tokens": event.projected_input_tokens,   # for the record only
                         "reserved_output_tokens": self.max_output_tokens,
                         "wrap_up_call": self.wrap_up}

    def after_model(self, event: AfterModelCallEvent) -> None:
        pending, self._pending = self._pending, None
        if pending is None:
            return   # the call was cancelled before sending: nothing was spent
        if event.exception is not None:
            # Failed call: it may or may not have been processed (and billed)
            LEDGER.settle(pending["ledger_id"], None, "model call failed; full reservation kept")
            LEDGER.halt(f"model call failed: {type(event.exception).__name__}")
            self.stop_reason = self.stop_reason or f"model call failed: {type(event.exception).__name__}"
            self.model_log.append({**pending, "status": "exception",
                                   "error": f"{type(event.exception).__name__}: {event.exception}"})
            return
        message = (event.stop_response.message if event.stop_response else None) or {}
        usage = (message.get("metadata") or {}).get("usage") or {}
        tin, tout = usage.get("inputTokens"), usage.get("outputTokens")
        if tin is None or tout is None or (self.strict and (tin or 0) + (tout or 0) == 0):
            LEDGER.settle(pending["ledger_id"], None, "no usage numbers; full reservation kept")
            LEDGER.halt("missing model cost accounting")
            self.stop_reason = self.stop_reason or "missing model cost accounting"
            self.model_log.append({**pending, "status": "no_usage"})
            return
        LEDGER.settle(pending["ledger_id"], model_cost(tin, tout), "ok")
        self.used_tokens += tin + tout
        entry = {**pending, "status": "ok", "input_tokens": tin, "output_tokens": tout,
                 "stop_reason": event.stop_response.stop_reason if event.stop_response else None}
        if tin > pending["reserved_input_tokens"] or tout > pending["reserved_output_tokens"]:
            # Cannot happen if Bedrock enforces the context window and max_tokens; stop if it does
            entry["status"] = "exceeded_reservation"
            LEDGER.halt("model usage exceeded its reservation")
            self.stop_reason = self.stop_reason or "model usage exceeded its reservation"
        self.model_log.append(entry)

    # ------------------------------------------------------------------ tool guidance
    def before_tool(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use["name"]
        if name not in QUERY_TOOLS:
            return   # e.g. the structured-output tool that writes the report
        args = {k: v for k, v in (event.tool_use.get("input") or {}).items()}
        if self.stop_reason or LEDGER.halted:
            self._block(event, name, args, "stopped", "The run is stopping; do not call more tools.")
            return
        if self.ctx.tool_calls_used >= self.ctx.max_tool_calls:
            message = (f"Tool budget reached ({self.ctx.max_tool_calls} calls). Stop investigating "
                       "and write the report with the evidence you already have.")
            self._block(event, name, args, "budget", message)
            return
        # Fill defaults so equivalent calls compare equal
        defaults = {"state": None, "category": None}
        full_args = {**defaults, **args, "week": self.ctx.week}   # week is bound to the run
        earlier = self.ctx.previous_result(name, full_args)
        if earlier:
            message = (f"You already made this exact call; its result is {earlier}. "
                       "Use that result instead of calling again.")
            self._block(event, name, args, "duplicate", message)

    def _block(self, event, name, args, reason, message):
        event.cancel_tool = message
        self.blocked.append({"tool": name, "args": args, "reason": reason})
