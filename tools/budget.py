"""Batch cost ledger: reserve the worst-case cost of every paid call BEFORE sending it.

Why: a cap that is only checked after a call can be overshot by that call.
Here every Bedrock call and every Athena query first reserves its maximum possible
cost. If the reservation would push the batch past its ceiling, the call is not
sent. After the call, the reservation is replaced by the actual cost (or kept in
full when the actual cost is unknown, so the ledger never under-counts).

Any unsafe condition (access denied, uncertain execution, missing usage numbers,
cap risk) halts the ledger: every later paid call is refused, and the batch stops.

Settings (environment variables):
    WBR_BATCH_BUDGET_USD   dollar ceiling for the batch (empty = ledger off, e.g. local DuckDB tests)
    WBR_PRICE_IN_PER_M     model input price per million tokens  (default 0.035, Amazon Nova Micro)
    WBR_PRICE_OUT_PER_M    model output price per million tokens (default 0.14,  Amazon Nova Micro)
"""

import os
import threading

PRICE_IN_PER_M = float(os.environ.get("WBR_PRICE_IN_PER_M", "0.035"))
PRICE_OUT_PER_M = float(os.environ.get("WBR_PRICE_OUT_PER_M", "0.14"))

MIB = 1024 * 1024
ATHENA_USD_PER_TB = 5.0
ATHENA_MIN_BILLED_BYTES = 10 * 1000 * 1000      # 10 MB minimum per query
ATHENA_SCAN_CUTOFF_BYTES = 100 * MIB             # S04-WBR workgroup cutoff (100 MiB per query)
S3_ALLOWANCE_PER_QUERY_USD = 0.00002             # result-file PUT/GET requests, rounded up generously


def model_cost(input_tokens: int, output_tokens: int) -> float:
    return input_tokens / 1e6 * PRICE_IN_PER_M + output_tokens / 1e6 * PRICE_OUT_PER_M


def athena_cost(bytes_scanned: int) -> float:
    billed = max(bytes_scanned, ATHENA_MIN_BILLED_BYTES)
    return billed / 1024 ** 4 * ATHENA_USD_PER_TB


def athena_reservation() -> float:
    """Worst case for one query: a full 100 MiB scan (the workgroup cutoff) plus S3 requests."""
    return athena_cost(ATHENA_SCAN_CUTOFF_BYTES) + S3_ALLOWANCE_PER_QUERY_USD


class BudgetError(Exception):
    """A paid call was refused before being sent."""


class Ledger:
    def __init__(self, ceiling_usd: float | None):
        self.ceiling_usd = ceiling_usd
        self.spent_usd = 0.0
        self.reserved: dict[int, float] = {}
        self.entries: list[dict] = []   # every reservation and its settlement (evidence)
        self.halted: str | None = None
        self._next_id = 0
        self._lock = threading.Lock()

    def reset(self, ceiling_usd: float | None) -> None:
        """Start a fresh ledger (used by tests; one batch = one process otherwise)."""
        self.__init__(ceiling_usd)

    @property
    def active(self) -> bool:
        return self.ceiling_usd is not None

    def committed_usd(self) -> float:
        return self.spent_usd + sum(self.reserved.values())

    def halt(self, reason: str) -> None:
        # Only a paid batch (ledger on) is halted; local runs just end the current run
        if self.active and not self.halted:
            self.halted = reason

    def reserve(self, kind: str, usd: float, detail: str = "") -> int | None:
        """Reserve usd before a paid call. Raises BudgetError if it must not be sent."""
        if not self.active:
            return None
        with self._lock:
            if self.halted:
                raise BudgetError(f"Batch halted ({self.halted}); {kind} call not sent")
            if self.committed_usd() + usd > self.ceiling_usd:
                self.halt(f"cap risk: {kind} reservation ${usd:.6f} would pass the ${self.ceiling_usd} ceiling")
                raise BudgetError(f"Budget ceiling would be exceeded; {kind} call not sent")
            self._next_id += 1
            rid = self._next_id
            self.reserved[rid] = usd
            self.entries.append({"id": rid, "kind": kind, "detail": detail,
                                 "reserved_usd": usd, "actual_usd": None, "settled": False})
            return rid

    def settle(self, rid: int | None, actual_usd: float | None, note: str = "") -> None:
        """Replace a reservation with the actual cost. None = unknown: keep the full reservation."""
        if rid is None:
            return
        with self._lock:
            reserved = self.reserved.pop(rid, 0.0)
            charged = reserved if actual_usd is None else actual_usd
            self.spent_usd += charged
            entry = next(e for e in self.entries if e["id"] == rid)
            entry.update(actual_usd=actual_usd, charged_usd=charged, settled=True, note=note)

    def summary(self) -> dict:
        return {
            "ceiling_usd": self.ceiling_usd,
            "spent_usd_upper_estimate": round(self.spent_usd, 6),
            "still_reserved_usd": round(sum(self.reserved.values()), 6),
            "halted": self.halted,
            "entries": len(self.entries),
        }


def _ceiling_from_env() -> float | None:
    value = os.environ.get("WBR_BATCH_BUDGET_USD", "")
    return float(value) if value else None


LEDGER = Ledger(_ceiling_from_env())