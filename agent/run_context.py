"""Per-run state: the evidence log, the call history, and the tool budget."""

import json


class RunContext:
    """Everything one analyst run records.

    evidence_log: every tool envelope, keyed by request_id (what the verifier checks)
    calls:        the ordered list of tool calls (for the transcript and process checks)
    """

    def __init__(self, week: str, max_tool_calls: int = 25):
        self.week = week
        self.max_tool_calls = max_tool_calls
        self.evidence_log: dict[str, dict] = {}
        self.calls: list[dict] = []
        self._seen: dict[str, str] = {}   # call signature -> request_id

    @staticmethod
    def signature(tool_name: str, args: dict) -> str:
        """Identical tool + inputs give an identical signature (used to block repeats)."""
        return tool_name + ":" + json.dumps(args, sort_keys=True, default=str)

    @property
    def tool_calls_used(self) -> int:
        return len(self.calls)

    def previous_result(self, tool_name: str, args: dict) -> str | None:
        return self._seen.get(self.signature(tool_name, args))

    def record(self, tool_name: str, args: dict, envelope: dict, requested_week: str | None = None) -> None:
        request_id = envelope["request_id"]
        self.evidence_log[request_id] = envelope
        self._seen[self.signature(tool_name, args)] = request_id
        call = {"step": len(self.calls) + 1, "tool": tool_name, "args": args,
                "request_id": request_id, "status": envelope["status"],
                "warnings": [w["code"] for w in envelope.get("warnings", [])]}
        if requested_week:
            call["requested_week"] = requested_week   # model asked for another week; overridden
        self.calls.append(call)
