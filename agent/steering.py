"""Steering hooks: just-in-time guidance at the agent's decision points.

Instead of a longer prompt, these hooks step in only when needed:
- the tool budget is used up -> stop investigating and write the report
- an identical call is repeated -> point to the earlier result instead
- the token budget is used up -> one last model call to write the report, then stop
  (checked before each call, so a run can end at most two model calls past the cap)
"""

from strands.hooks import (AfterModelCallEvent, BeforeModelCallEvent, BeforeToolCallEvent,
                           HookProvider, HookRegistry)

from agent.run_context import RunContext

QUERY_TOOLS = {"get_weekly_metric", "get_breakdown", "get_delivery_signals"}


class AnalystSteering(HookProvider):
    def __init__(self, ctx: RunContext, max_tokens: int | None = None):
        self.ctx = ctx
        self.max_tokens = max_tokens    # cost guard: total tokens per run (None = no cap)
        self.blocked: list[dict] = []   # recorded for the transcript
        self.model_calls = 0
        self.model_calls_cancelled = 0
        self.token_cap_hit = False
        self._final_calls_left = 1      # after the cap: one call allowed to write the report

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(BeforeModelCallEvent, self.before_model)
        registry.add_callback(AfterModelCallEvent, self.after_model)

    def before_model(self, event: BeforeModelCallEvent) -> None:
        if self.max_tokens is None:
            return
        used = event.agent.event_loop_metrics.accumulated_usage.get("totalTokens", 0)
        if used < self.max_tokens:
            return
        if not self.token_cap_hit:
            # First time over the cap: no more investigating, tools are blocked too
            self.token_cap_hit = True
            self.ctx.max_tool_calls = self.ctx.tool_calls_used
            event.cancel = (f"Token budget reached ({used} of {self.max_tokens}). "
                            "Write the report now with the evidence you already have.")
            self.model_calls_cancelled += 1
            return
        if self._final_calls_left > 0:
            self._final_calls_left -= 1   # let the report be written
            return
        event.cancel = "Token budget exhausted; no further model calls."
        self.model_calls_cancelled += 1

    def after_model(self, event: AfterModelCallEvent) -> None:
        self.model_calls += 1

    def before_tool(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use["name"]
        if name not in QUERY_TOOLS:
            return   # e.g. the structured-output tool that writes the report
        args = {k: v for k, v in (event.tool_use.get("input") or {}).items()}
        if self.ctx.tool_calls_used >= self.ctx.max_tool_calls:
            message = (f"Tool budget reached ({self.ctx.max_tool_calls} calls). Stop investigating "
                       "and write the report with the evidence you already have.")
            self._block(event, name, args, "budget", message)
            return
        # Fill defaults so equivalent calls compare equal
        defaults = {"state": None, "category": None}
        full_args = {**defaults, **args}
        earlier = self.ctx.previous_result(name, full_args)
        if earlier:
            message = (f"You already made this exact call; its result is {earlier}. "
                       "Use that result instead of calling again.")
            self._block(event, name, args, "duplicate", message)

    def _block(self, event, name, args, reason, message):
        event.cancel_tool = message
        self.blocked.append({"tool": name, "args": args, "reason": reason})