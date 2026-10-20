"""Model adapters for model-backed workflow runs.

A `ModelAdapter` turns one assembled request (system prompt + user prompt) into a structured answer plus the
usage the provider reported. The dispatcher calls it with exactly this signature:

    generate(system_prompt, user_prompt, *, expected_schema=None, temperature=0.0) -> (result, TaskUsage)

* `result` is a dict or a JSON string; the dispatcher validates it (`model_io.validate_model_output`) and never
  trusts it blindly.
* `TaskUsage` carries only what the provider (or a scripted double) actually reported. `input_tokens` is the
  total prompt size (cached + uncached); `cache_read_tokens` and `cache_creation_tokens` are portions of it.
  A figure the provider did not report stays `None`; nothing is estimated here.
* `describe()` is the adapter's identity for cache fingerprints: provider, model id and every setting that can
  change an answer. Two adapters with equal `describe()` are interchangeable.

Two implementations ship:
* `ScriptedModelAdapter`: deterministic double for tests and CI. No network.
* `AnthropicMessagesAdapter`: optional live adapter (`pip install "research-harness[model]"`). Never constructed
  implicitly; nothing in the harness makes a network call unless the caller passes it in.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from research_harness.workflows.contracts import TaskUsage
from research_harness.workflows.instrumentation import estimate_tokens


class ModelError(Exception):
    """Base class for adapter failures that still carry the usage of the call that happened."""

    def __init__(self, message: str, *, usage: Optional[TaskUsage] = None, partial_output: Optional[str] = None):
        super().__init__(message)
        self.usage = usage
        self.partial_output = partial_output


class ModelRefusal(ModelError):
    """The provider's safeguards declined the request (`stop_reason == "refusal"`)."""

    def __init__(self, message: str, *, category: Optional[str] = None, **kw: Any):
        super().__init__(message, **kw)
        self.category = category


class ModelOutputTruncated(ModelError):
    """The answer hit the output-token ceiling, so it cannot be complete."""


@runtime_checkable
class ModelAdapter(Protocol):
    """Protocol for model backends capable of reasoning over structured research context."""

    model_id: str

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        expected_schema: Optional[dict[str, Any]] = None,
        temperature: float = 0.0,
    ) -> tuple[Any, TaskUsage]:
        """Execute one request; return the structured answer and the usage the provider reported."""
        ...


def adapter_identity(adapter: Any) -> dict[str, Any]:
    """Stable, JSON-serializable identity of an adapter for cache fingerprints.

    Uses `describe()` when the adapter has it. Otherwise falls back to class name + model id: never the
    object's `repr`, which embeds a memory address and so differs between identical instances.
    """
    describe = getattr(adapter, "describe", None)
    if callable(describe):
        return dict(describe())
    cls = type(adapter)
    return {"provider": f"{cls.__module__}.{cls.__qualname__}", "model_id": getattr(adapter, "model_id", None)}


def adapter_token_count(adapter: Any, system_prompt: str, user_prompt: str) -> tuple[int, str]:
    """Token count of the full request and its basis (`adapter` when the adapter counts exactly, otherwise the
    local estimate, labelled `tiktoken` or `heuristic`)."""
    counter = getattr(adapter, "count_tokens", None)
    if callable(counter):
        exact = counter(system_prompt, user_prompt)
        if isinstance(exact, int) and exact >= 0:
            return exact, "adapter"
    count, is_estimate = estimate_tokens(system_prompt + "\n" + user_prompt)
    return count, "heuristic" if is_estimate else "tiktoken"


# ---------------------------------------------------------------------------------------------------
# scripted double
# ---------------------------------------------------------------------------------------------------

@dataclass
class RecordedRequest:
    """Audit record of a model request."""
    system_prompt: str
    user_prompt: str
    expected_schema: Optional[dict[str, Any]]
    temperature: float


class ScriptedModelAdapter:
    """Scripted adapter for deterministic tests and CI.

    Records every assembled request and returns a predetermined answer (`default_response`, a dict or a raw
    string) or the result of `custom_handler(system_prompt, user_prompt)`. Its usage is labelled `scripted`,
    never `provider`.
    """

    ADAPTER_VERSION = "1"

    def __init__(
        self,
        model_id: str = "mock-reasoning-model-v1",
        default_response: Any = None,
        custom_handler: Optional[Callable[[str, str], Any]] = None,
        input_token_cost: Optional[int] = None,
        output_token_cost: Optional[int] = None,
    ):
        self.model_id = model_id
        self.default_response = {} if default_response is None else default_response
        self.custom_handler = custom_handler
        self.input_token_cost = input_token_cost
        self.output_token_cost = output_token_cost
        self.recorded_requests: list[RecordedRequest] = []

    def describe(self) -> dict[str, Any]:
        if self.custom_handler is not None:
            script = f"handler:{getattr(self.custom_handler, '__module__', '?')}.{getattr(self.custom_handler, '__qualname__', '?')}"
        else:
            script = "response:" + hashlib.sha256(
                json.dumps(self.default_response, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
        return {
            "provider": "scripted",
            "model_id": self.model_id,
            "adapter_version": self.ADAPTER_VERSION,
            "script": script,
            "input_token_cost": self.input_token_cost,
            "output_token_cost": self.output_token_cost,
        }

    def count_tokens(self, system_prompt: str, user_prompt: str) -> Optional[int]:
        return self.input_token_cost  # None => the dispatcher falls back to its local estimate

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        expected_schema: Optional[dict[str, Any]] = None,
        temperature: float = 0.0,
    ) -> tuple[Any, TaskUsage]:
        self.recorded_requests.append(RecordedRequest(system_prompt, user_prompt, expected_schema, temperature))

        if self.custom_handler:
            result = self.custom_handler(system_prompt, user_prompt)
        elif isinstance(self.default_response, dict):
            result = dict(self.default_response)
        else:
            result = self.default_response

        in_tokens = self.input_token_cost
        if in_tokens is None:
            in_tokens, _ = estimate_tokens(f"{system_prompt}\n{user_prompt}")
        out_tokens = self.output_token_cost
        if out_tokens is None:
            out_tokens, _ = estimate_tokens(result if isinstance(result, str) else json.dumps(result, default=str))

        usage = TaskUsage(
            workflow="model_adapted",
            task_id="TASK_MODEL",
            model=self.model_id,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            cache_read_tokens=None,
            llm_calls=1,
            context_objects=0,
            reused_artifacts=0,
            elapsed_ms=10,
            usage_source="scripted",
        )
        return result, usage


# ---------------------------------------------------------------------------------------------------
# live adapter (optional)
# ---------------------------------------------------------------------------------------------------

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models for which the server-side refusal fallback is documented. Elsewhere the parameter is not sent.
_FALLBACK_MODELS = frozenset({"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"})


class AnthropicMessagesAdapter:
    """Live adapter over the Anthropic Messages API.

    * The model id is configuration, never a default: pass `model_id` or set `RESEARCH_MODEL`.
    * Sampling parameters are not sent (current models reject non-default values and the SDK no longer
      exposes `temperature`); the protocol's `temperature` argument is accepted and ignored.
    * Thinking is left at the model default. Depth is controlled by `effort` (`output_config.effort`); pass
      `effort=None` for models that do not support it.
    * The system prompt (skill + output contract) is the stable prefix and is marked cacheable, so repeated
      runs of one workflow can show `cache_read_tokens`.
    * `use_fallbacks` (default: on for the models documented to support it) asks the API to re-run a request
      that safeguards decline on a fallback model, server-side. Needs the beta endpoint.
    * The answer is requested as plain JSON text and validated by the dispatcher; structured-output
      constraints are deliberately not used because the result schema is open (`additionalProperties`).

    `client` may be injected (a fake in tests, or a pre-configured SDK client). This adapter was exercised
    against a fake client and the SDK's real call signatures, not against the live API.
    """

    ADAPTER_VERSION = "1"

    def __init__(
        self,
        model_id: Optional[str] = None,
        *,
        client: Any = None,
        max_output_tokens: int = 16000,
        effort: Optional[str] = "high",
        cache_system_prompt: bool = True,
        use_fallbacks: Optional[bool] = None,
    ):
        resolved = model_id or os.environ.get("RESEARCH_MODEL")
        if not resolved:
            raise ValueError(
                "AnthropicMessagesAdapter needs a model id: pass model_id=... or set RESEARCH_MODEL. "
                "The model is part of every run's identity, so it is never defaulted."
            )
        self.model_id = resolved
        self.max_output_tokens = max_output_tokens
        self.effort = effort
        self.cache_system_prompt = cache_system_prompt
        self.use_fallbacks = (resolved in _FALLBACK_MODELS) if use_fallbacks is None else use_fallbacks
        if client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - depends on the environment
                raise ImportError(
                    'The live adapter needs the Anthropic SDK: pip install "research-harness[model]"') from exc
            client = anthropic.Anthropic()
        self._client = client

    def describe(self) -> dict[str, Any]:
        return {
            "provider": "anthropic",
            "model_id": self.model_id,
            "adapter_version": self.ADAPTER_VERSION,
            "max_output_tokens": self.max_output_tokens,
            "effort": self.effort,
            "fallbacks": self.use_fallbacks,
            "cache_system_prompt": self.cache_system_prompt,
            "temperature_sent": False,
        }

    # -- request pieces -----------------------------------------------------------------------------
    def _system_blocks(self, system_prompt: str) -> list[dict[str, Any]]:
        block: dict[str, Any] = {"type": "text", "text": system_prompt}
        if self.cache_system_prompt:
            block["cache_control"] = {"type": "ephemeral"}
        return [block]

    def count_tokens(self, system_prompt: str, user_prompt: str) -> Optional[int]:
        try:
            counted = self._client.messages.count_tokens(
                model=self.model_id,
                system=self._system_blocks(system_prompt),
                messages=[{"role": "user", "content": user_prompt}],
            )
            return int(counted.input_tokens)
        except Exception:  # noqa: BLE001  (counting is advisory; the caller falls back to an estimate)
            return None

    @staticmethod
    def _usage(resp: Any, model_id: str) -> TaskUsage:
        u = getattr(resp, "usage", None)
        uncached = getattr(u, "input_tokens", None)
        read = getattr(u, "cache_read_input_tokens", None)
        created = getattr(u, "cache_creation_input_tokens", None)
        total_in = None if uncached is None else int(uncached) + int(read or 0) + int(created or 0)
        out = getattr(u, "output_tokens", None)
        return TaskUsage(
            workflow="model_adapted", task_id="TASK_MODEL", model=getattr(resp, "model", None) or model_id,
            input_tokens=total_in, output_tokens=None if out is None else int(out),
            cache_read_tokens=None if read is None else int(read),
            cache_creation_tokens=None if created is None else int(created),
            llm_calls=1, usage_source="provider",
        )

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        expected_schema: Optional[dict[str, Any]] = None,
        temperature: float = 0.0,
    ) -> tuple[Any, TaskUsage]:
        request: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self.max_output_tokens,
            "system": self._system_blocks(system_prompt),
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if self.effort:
            request["output_config"] = {"effort": self.effort}
        if self.use_fallbacks:
            resp = self._client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **request)
        else:
            resp = self._client.messages.create(**request)

        usage = self._usage(resp, self.model_id)
        text = "".join(getattr(b, "text", "") for b in getattr(resp, "content", []) if getattr(b, "type", None) == "text")
        stop = getattr(resp, "stop_reason", None)
        if stop == "refusal":
            details = getattr(resp, "stop_details", None)
            raise ModelRefusal("the provider declined the request", category=getattr(details, "category", None),
                               usage=usage, partial_output=text or None)
        if stop == "max_tokens":
            raise ModelOutputTruncated(
                f"output reached max_output_tokens={self.max_output_tokens}", usage=usage, partial_output=text or None)
        return text, usage
