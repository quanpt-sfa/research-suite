"""Token instrumentation and budgeting for research workflows.

Tracks token consumption, model calls, context object counts, artifact reuse,
and execution time per task without requiring live LLM calls for status or listing.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from research_harness.state.store import StateStore
from research_harness.workflows.contracts import TaskUsage, TokenBudget


def estimate_tokens(text: str) -> Tuple[int, bool]:
    """Estimate token count for a text string.
    
    Returns (token_count, is_estimate).
    If provider-grade tokenizer (tiktoken) is available, uses exact BPE encoding.
    Otherwise uses an accurate BPE heuristic (~3.8 chars/token) and marks as estimate.
    """
    if not text:
        return 0, False

    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text)), False
    except Exception:
        # Standard cl100k heuristic: ~3.8 - 4.0 characters per token for English scholarly text
        # Count words and punctuation clusters for realistic estimate
        tokens = max(1, math.ceil(len(text) / 3.8))
        return tokens, True


class UsageTracker:
    """Manages persistent usage logging and budget enforcement for a project."""

    def __init__(self, store: Optional[StateStore] = None):
        self.store = store
        self.log_file = store.paths.root / "workflow_usage.jsonl" if store is not None else None
        self._in_memory_records: list[dict[str, Any]] = []

    def record_task(self, usage: TaskUsage) -> None:
        """Atomically append a task usage record to the persistent log or memory."""
        rec = usage.to_dict()
        self._in_memory_records.append(rec)
        if self.log_file is not None:
            line = json.dumps(rec, sort_keys=True) + "\n"
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line)

    def get_records(self, workflow: Optional[str] = None) -> list[dict[str, Any]]:
        """Retrieve all recorded usage entries, optionally filtered by workflow."""
        if self.log_file is None or not self.log_file.exists():
            return [r for r in self._in_memory_records if workflow is None or r.get("workflow") == workflow]

        out = []
        with open(self.log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if workflow is None or rec.get("workflow") == workflow:
                        out.append(rec)
                except Exception:
                    continue
        return out

    def get_summary(self, workflow: Optional[str] = None) -> dict[str, Any]:
        """Aggregate total usage statistics across recorded tasks."""
        records = self.get_records(workflow=workflow)
        total_input = sum(r.get("input_tokens") or 0 for r in records)
        total_output = sum(r.get("output_tokens") or 0 for r in records)
        total_cache = sum(r.get("cache_read_tokens") or 0 for r in records)
        total_calls = sum(r.get("llm_calls") or 0 for r in records)
        total_reused = sum(r.get("reused_artifacts") or 0 for r in records)
        total_ms = sum(r.get("elapsed_ms") or 0 for r in records)

        by_workflow: dict[str, dict[str, Any]] = {}
        for r in records:
            w = r.get("workflow", "unknown")
            if w not in by_workflow:
                by_workflow[w] = {
                    "tasks_count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "llm_calls": 0,
                    "reused_artifacts": 0,
                }
            by_workflow[w]["tasks_count"] += 1
            by_workflow[w]["input_tokens"] += (r.get("input_tokens") or 0)
            by_workflow[w]["output_tokens"] += (r.get("output_tokens") or 0)
            by_workflow[w]["llm_calls"] += (r.get("llm_calls") or 0)
            by_workflow[w]["reused_artifacts"] += (r.get("reused_artifacts") or 0)

        return {
            "project_id": self.store.project_id if self.store is not None else "OFFLINE",
            "tasks_recorded": len(records),
            "total_tasks": len(records),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cache_read_tokens": total_cache,
            "total_llm_calls": total_calls,
            "total_reused_artifacts": total_reused,
            "total_elapsed_ms": total_ms,
            "by_workflow": by_workflow,
        }
