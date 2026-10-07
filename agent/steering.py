"""Steering hooks: just-in-time guidance at the agent's decision points.

Instead of a longer prompt, these hooks step in only when needed:
- the tool budget is used up -> stop investigating and write the report
- an identical call is repeated -> point to the earlier result instead
"""

from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

from agent.run_context import RunContext

QUERY_TOOLS = {"get_weekly_metric", "get_breakdown", "get_delivery_signals"}


class AnalystSteering(HookProvider):
    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self.blocked: list[dict] = []   # recorded for the transcript

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool)

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
