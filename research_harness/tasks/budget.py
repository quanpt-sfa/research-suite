"""A hard token budget for the model calls of a task (M5 Amendment 1 A8, contract C3).

Every call is wrapped: its input is counted with an upper bound, its output is capped by what the budget still allows (the adapter's
`max_output_tokens`), and an intent record is written before the call. When the call returns (or fails) its charge is written to the run
registry as a `task_call:charged` row, and only then is the intent removed; the budget is read from those rows, so what was charged survives a
restart whether or not the provider reported usage, and the runs the calls were made for are never counted again. A call whose outcome is
unknown (the process died between the intent and the charge) is charged at its full reservation when the task next runs.

Limits that remain, stated rather than hidden:
* an adapter without a `max_output_tokens` attribute (the scripted double) cannot be capped; it is reserved a bounded answer and charged what it reports;
* a provider may bill a request that completed after the client timed out, or bill differently from its reported usage;
* money cannot be capped by tokens alone: reports show tokens, and money only with a price table;
* if the charge row cannot be written after a call, the intent stays and the call is charged again at its reservation on the next run (conservative).
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from research_harness.core.canonical import canonical_json_hash
from research_harness.state import workflow_runs
from research_harness.workflows.adapters import ModelError, adapter_identity, adapter_token_count

MIN_USEFUL_OUTPUT = 256        # a call that cannot be given at least this many output tokens is not started
UPPER_BOUND_FACTOR = 1.25      # input counts that are not exact (a heuristic or a local tokenizer) are inflated by this
UNCAPPED_OUTPUT_RESERVE = 8000  # what is reserved for the answer of an adapter whose output cannot be capped
NEAR_LIMIT = 0.9               # no new step is scheduled once this share of the token budget is spent


class BudgetExceeded(ModelError):
    """The call was not made: it would break the task's budget."""


@dataclass
class Ledger:
    max_tokens: Optional[int]
    max_calls: Optional[int]
    spent_tokens: int = 0
    spent_calls: int = 0
    blocked_by: Optional[str] = None

    def remaining_tokens(self) -> Optional[int]:
        return None if self.max_tokens is None else self.max_tokens - self.spent_tokens

    def near_limit(self) -> bool:
        return self.max_tokens is not None and self.spent_tokens >= NEAR_LIMIT * self.max_tokens

    def add(self, tokens: int, calls: int = 1) -> None:
        self.spent_tokens += tokens
        self.spent_calls += calls


def attempts_dir(store: Any, task_id: str) -> Path:
    d = store.paths.staging / "attempts" / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def outstanding_intents(store: Any, task_id: str) -> list[dict[str, Any]]:
    """Calls that were started and not yet accounted: an intent record whose charge row was never written."""
    d = store.paths.staging / "attempts" / task_id
    out = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("status") == "INTENT":
            out.append({**rec, "_path": str(p)})
    return out


def mark_accounted(intent: dict[str, Any]) -> None:
    Path(intent["_path"]).unlink(missing_ok=True)


def _tokens(usage: Any) -> tuple[Optional[int], Optional[int]]:
    return getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)


class BudgetedAdapter:
    """Delegates to `inner`, enforcing the ledger on every `generate` call and writing every call's charge to the registry."""

    def __init__(self, inner: Any, ledger: Ledger, store: Any, task_id: str, step_id: str):
        self._inner, self._ledger, self._store, self._task_id, self._step_id = inner, ledger, store, task_id, step_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _persist_charge(self, path: Path, *, outcome: str, usage: Any, in_reserved: int, reserved_out: int) -> int:
        """The charge of one call, written before its intent is removed. Reported usage is charged as reported; a call whose usage the provider
        did not report (or that failed without it) is charged at its reservation."""
        tin, tout = _tokens(usage)
        reported = tin is not None or tout is not None
        in_tok, out_tok = (int(tin or 0), int(tout or 0)) if reported else (in_reserved, reserved_out)
        charged = in_tok + out_tok
        row = {"workflow": "task_call:charged", "input_fingerprint": canonical_json_hash({"intent": path.name, "task": self._task_id}),
               "executor": "task_engine@1", "execution_status": "SUCCEEDED" if outcome == "ok" else "FAILED", "context_status": "COMPLETE",
               "assessment_status": "NOT_ASSESSED", "applicability": "APPLICABLE", "legacy_status": "SUCCESS" if outcome == "ok" else "FAILED",
               "status_reason": outcome,
               "usage": {"llm_calls": 1, "input_tokens": in_tok, "output_tokens": out_tok, "charged_tokens": charged,
                         "usage_source": "provider_reported" if reported else "reserved"},
               "model_descriptor": adapter_identity(self._inner), "task_id": self._task_id, "task_step_id": self._step_id}
        try:
            workflow_runs.append_run(self._store, row)
        except Exception:  # noqa: BLE001 - keep the intent: the next run charges this call at its reservation
            return charged
        path.unlink(missing_ok=True)
        return charged

    def generate(self, system_prompt: str, user_prompt: str, *, expected_schema: Optional[dict[str, Any]] = None, temperature: float = 0.0):
        led = self._ledger
        count, basis = adapter_token_count(self._inner, system_prompt, user_prompt)
        in_reserved = count if basis == "adapter" else int(count * UPPER_BOUND_FACTOR) + 1
        if led.max_calls is not None and led.spent_calls + 1 > led.max_calls:
            led.blocked_by = "max_llm_calls"
            raise BudgetExceeded(f"the task's budget of {led.max_calls} model calls is used up")
        ceiling = getattr(self._inner, "max_output_tokens", None)
        out_cap = ceiling
        remaining = led.remaining_tokens()
        if remaining is not None:
            allowed = remaining - in_reserved
            if allowed < MIN_USEFUL_OUTPUT:
                led.blocked_by = "max_tokens"
                raise BudgetExceeded(f"{remaining} tokens remain; this call needs {in_reserved} for its input and at least {MIN_USEFUL_OUTPUT} for its answer")
            out_cap = min(ceiling, allowed) if ceiling else None       # an uncapped adapter is reserved a bounded answer below
        reserved_out = int(out_cap) if out_cap else (min(UNCAPPED_OUTPUT_RESERVE, remaining - in_reserved) if remaining is not None else UNCAPPED_OUTPUT_RESERVE)
        reservation = in_reserved + reserved_out
        path = attempts_dir(self._store, self._task_id) / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}_{secrets.token_hex(3)}.json"
        _write_atomic(path, {"status": "INTENT", "task_id": self._task_id, "step_id": self._step_id, "at": datetime.now(timezone.utc).isoformat(),
                             "input_reserved": in_reserved, "output_reserved": reserved_out, "reservation": reservation, "count_basis": basis,
                             "model": adapter_identity(self._inner)})
        previous = getattr(self._inner, "max_output_tokens", None)
        if hasattr(self._inner, "max_output_tokens") and out_cap:
            self._inner.max_output_tokens = int(out_cap)
        try:
            result, usage = self._inner.generate(system_prompt, user_prompt, expected_schema=expected_schema, temperature=temperature)
        except ModelError as exc:
            led.add(self._persist_charge(path, outcome=f"error:{type(exc).__name__}", usage=getattr(exc, "usage", None),
                                         in_reserved=in_reserved, reserved_out=reserved_out))
            raise
        finally:
            if hasattr(self._inner, "max_output_tokens") and previous is not None:
                self._inner.max_output_tokens = previous
        led.add(self._persist_charge(path, outcome="ok", usage=usage, in_reserved=in_reserved, reserved_out=reserved_out))
        return result, usage
