"""Model adapter interfaces and test fixtures for Research Suite 9.1.1.

Provides a lightweight, zero-dependency model abstraction:
- ModelAdapter protocol
- ScriptedModelAdapter for deterministic testing and reproducible CI
- OfflineExecutionAdapter
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

from research_harness.workflows.contracts import TaskUsage
from research_harness.workflows.instrumentation import estimate_tokens


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
    ) -> tuple[dict[str, Any], TaskUsage]:
        """Execute reasoning request and return structured response with exact provider usage."""
        ...


@dataclass
class RecordedRequest:
    """Audit record of a model request."""
    system_prompt: str
    user_prompt: str
    expected_schema: Optional[dict[str, Any]]
    temperature: float


class ScriptedModelAdapter:
    """Mock/Scripted model adapter for deterministic tests and CI.

    Records every assembled request and returns predetermined responses or
    generates responses via a custom handler function.
    """

    def __init__(
        self,
        model_id: str = "mock-reasoning-model-v1",
        default_response: Optional[dict[str, Any]] = None,
        custom_handler: Optional[Callable[[str, str], dict[str, Any]]] = None,
        input_token_cost: Optional[int] = None,
        output_token_cost: Optional[int] = None,
    ):
        self.model_id = model_id
        self.default_response = default_response or {}
        self.custom_handler = custom_handler
        self.input_token_cost = input_token_cost
        self.output_token_cost = output_token_cost
        self.recorded_requests: list[RecordedRequest] = []

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        expected_schema: Optional[dict[str, Any]] = None,
        temperature: float = 0.0,
    ) -> tuple[dict[str, Any], TaskUsage]:
        # Record request for audit and assertion
        req = RecordedRequest(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            expected_schema=expected_schema,
            temperature=temperature,
        )
        self.recorded_requests.append(req)

        if self.custom_handler:
            result = self.custom_handler(system_prompt, user_prompt)
        else:
            result = dict(self.default_response)

        in_tokens = self.input_token_cost
        if in_tokens is None:
            combined_prompt = f"{system_prompt}\n{user_prompt}"
            in_tokens, _ = estimate_tokens(combined_prompt)

        out_tokens = self.output_token_cost
        if out_tokens is None:
            out_tokens, _ = estimate_tokens(json.dumps(result))

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
            usage_source="provider",
        )
        return result, usage
