"""A scripted stand-in model for testing the agent loop without Bedrock.

Each step is either a tool call or a function that builds one from the conversation
so far (so a step can cite request_ids returned by earlier tools). Used only in tests.
"""

import json

from strands.models import Model


def request_ids(messages) -> list[str]:
    """Every request_id returned in tool results so far, in order."""
    ids = []
    for msg in messages:
        for block in msg.get("content", []):
            for item in block.get("toolResult", {}).get("content", []):
                payload = item.get("json")
                if payload is None and "text" in item:
                    try:
                        payload = json.loads(item["text"])
                    except (TypeError, ValueError):
                        payload = None
                if isinstance(payload, dict) and "request_id" in payload:
                    ids.append(payload["request_id"])
    return ids


class ScriptedModel(Model):
    def __init__(self, steps, tokens_per_call: int = 0):
        self.steps = list(steps)
        self.config = {}
        self.tokens_per_call = tokens_per_call  # fake usage, to test the token budget

    def update_config(self, **kwargs):
        self.config.update(kwargs)

    def get_config(self):
        return self.config

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        names = [t["name"] for t in (tool_specs or [])]
        step = self.steps.pop(0) if self.steps else {"report": {"claims": [], "actionable_questions": [],
                                                                 "unavailable": ["script ended"]}}
        if callable(step):
            step = step(messages)
        if "report" in step:   # the structured-output tool is the one that is not a query tool
            name = next(n for n in names if n not in
                        ("get_weekly_metric", "get_breakdown", "get_delivery_signals"))
            tool_input = step["report"]
        else:
            name, tool_input = step["tool"], step["input"]
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {"toolUse": {"name": name, "toolUseId": f"t{len(messages)}"}}}}
        yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(tool_input)}}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}
        n = self.tokens_per_call
        yield {"metadata": {"usage": {"inputTokens": n, "outputTokens": 0, "totalTokens": n},
                            "metrics": {"latencyMs": 0}}}