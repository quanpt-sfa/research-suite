"""Selective workflow dispatcher, orchestration, and artifact caching.

Inspired by Deep Agents isolated contexts and progressive disclosure:
- Selective orchestration: runs a single workflow or specified sequence.
- Reuses cached outputs when input revisions and configuration have not changed; incomplete or invalid
  outputs are never cached.
- Records task instrumentation into the project's persistent usage log.
- Supports structured cross-workflow handoffs (Results -> Discussion -> Contribution).
- Model-backed execution through an injectable `ModelAdapter` (see `adapters.py`):
    * the request is built from the selected skill, an output contract and the budgeted evidence;
    * a request that does not fit `max_input_tokens` is never sent;
    * the answer must satisfy the schema, cite only evidence present in the request, and quote it verbatim
      (`model_io.validate_model_output`); otherwise the run is INVALID_OUTPUT;
    * usage is exactly what the adapter reported, nothing is estimated or defaulted;
    * a valid model answer is PROVISIONAL, never verified (it has not been adjudicated).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from research_harness.state.store import StateStore
from research_harness.workflows.adapters import (
    ModelOutputTruncated,
    ModelRefusal,
    adapter_identity,
    adapter_token_count,
)
from research_harness.workflows.context import build_context
from research_harness.workflows.contracts import ContextPackage, TaskUsage, WorkflowOutput
from research_harness.workflows.instrumentation import UsageTracker
from research_harness.workflows.model_io import (
    CONTRACT_VERSION,
    build_system_prompt,
    detect_gate_conflicts,
    output_schema,
    schema_digest,
    validate_model_output,
)
from research_harness.workflows.registry import get_registry
from research_harness.workflows.skills.contribution import execute_contribution_workflow
from research_harness.workflows.skills.discussion import execute_discussion_workflow
from research_harness.workflows.skills.literature import execute_literature_workflow
from research_harness.workflows.skills.methods import execute_methods_workflow
from research_harness.workflows.skills.results import execute_results_workflow
from research_harness.workflows.status import (
    Applicability,
    AssessmentStatus,
    ContextStatus,
    ExecutionStatus,
    Status,
    apply_caps,
    axes_from_legacy,
)

_EXECUTORS = {
    "contribution": execute_contribution_workflow,
    "literature": execute_literature_workflow,
    "methods": execute_methods_workflow,
    "results": execute_results_workflow,
    "discussion": execute_discussion_workflow,
}

# Only outputs that are complete enough to stand in for a re-run are cached. INCOMPLETE_CONTEXT,
# INVALID_OUTPUT and FAILED are recomputed every time.
_CACHEABLE_STATUSES = frozenset({"SUCCESS", "REQUIRES_VERIFICATION"})
_ADAPTER_KEYS = ("model_adapter", "adapter")
_MAX_RAW_OUTPUT_CHARS = 20000


class WorkflowPersistenceError(RuntimeError):
    """A workflow output could not be written to the content-addressed store."""


def _compute_input_fingerprint(
    workflow: str,
    context: ContextPackage,
    extra_config: Optional[dict[str, Any]] = None,
) -> str:
    """Compute deterministic SHA-256 fingerprint over all input object revisions, skills, full-text policies, and configuration."""
    safe_config: dict[str, Any] = {}
    upstream_digests: list[str] = []
    adapter: Any = None
    for key in _ADAPTER_KEYS:
        if extra_config and extra_config.get(key) is not None:
            adapter = extra_config[key]
    if extra_config:
        for k, v in extra_config.items():
            if k in _ADAPTER_KEYS:
                continue
            if k == "upstream_artifacts" and isinstance(v, (list, tuple)):
                for art in v:
                    if hasattr(art, "output_digest"):
                        upstream_digests.append(art.output_digest)
                    elif isinstance(art, dict) and "output_digest" in art:
                        upstream_digests.append(art["output_digest"])
                    elif hasattr(art, "to_dict"):
                        upstream_digests.append(
                            hashlib.sha256(json.dumps(art.to_dict(), sort_keys=True).encode()).hexdigest()
                        )
                    else:
                        upstream_digests.append(str(art))
            elif hasattr(v, "to_dict"):
                safe_config[k] = v.to_dict()
            elif isinstance(v, (str, int, float, bool, type(None), list, dict)):
                try:
                    json.dumps(v)
                    safe_config[k] = v
                except Exception:
                    safe_config[k] = str(v)
            else:
                safe_config[k] = str(v)

    obj_fingerprints = sorted(
        f"{o['object_id']}:rev{o['revision']}:{o.get('content_digest', '')}"
        for o in context.objects
    )

    fulltext_fingerprints = sorted(
        f"{ft.get('source_id')}:{ft.get('fulltext_status')}:{hashlib.sha256((ft.get('full_text') or '').encode()).hexdigest()}"
        for ft in context.full_texts
    )

    omitted_fingerprints = sorted(
        f"{om.get('object_id')}:rev{om.get('revision')}:{om.get('reason')}"
        for om in context.omitted_items
    )

    payload = {
        "workflow": workflow,
        "workflow_version": "9.1.1",
        "output_schema_version": "1.0",
        "skill_digest": context.skill_digest,
        "rq_id": context.research_question_id,
        "focus_claims": sorted(context.focus_claims),
        "object_revisions": obj_fingerprints,
        "fulltext_fingerprints": fulltext_fingerprints,
        "omitted_fingerprints": omitted_fingerprints,
        "budget_limit": context.manifest.get("budget_limit"),
        "budget_status": context.manifest.get("budget_status"),
        "profile": context.manifest.get("profile"),
        "upstream_artifacts": upstream_digests,
        "extra_config": safe_config,
        # Who produced the answer. An adapter is identified by its declared configuration (never by repr()),
        # so identical adapters share cache entries and any change of model or setting is a miss.
        "executor": "deterministic" if adapter is None else "model",
        "model": None if adapter is None else adapter_identity(adapter),
        "model_contract": None if adapter is None else {"version": CONTRACT_VERSION, "schema": schema_digest(workflow)},
    }
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_cache_dir(store: StateStore) -> Path:
    cd = store.paths.root / "workflow_cache"
    cd.mkdir(parents=True, exist_ok=True)
    return cd


# ---------------------------------------------------------------------------------------------------
# model-backed execution
# ---------------------------------------------------------------------------------------------------

def _gate_summary(gates: WorkflowOutput) -> dict[str, Any]:
    """The part of the deterministic evidence gate a model answer may not contradict."""
    res = gates.result or {}
    summary: dict[str, Any] = {"status": gates.status}
    for key in ("identification_status", "novelty_status", "missing_verification", "missing_fulltext_competitors",
                "questions_requiring_fulltext_verification", "limitations_and_unresolved_choices"):
        if key in res:
            summary[key] = res[key]
    repro = res.get("reproducibility_metadata")
    if isinstance(repro, dict) and "reproducibility_status" in repro:
        summary["reproducibility_status"] = repro["reproducibility_status"]
    return summary


def _normalize_usage(reported: Any, *, workflow: str, task_id: str, context: ContextPackage, model_id: Optional[str],
                     elapsed_ms: int) -> TaskUsage:
    """Re-label what an adapter reported with this run's identity. Token figures are copied verbatim; a figure the
    adapter did not report stays None (nothing is estimated or defaulted here)."""
    common = dict(workflow=workflow, task_id=task_id, context_objects=len(context.objects), reused_artifacts=0,
                  elapsed_ms=elapsed_ms)
    if isinstance(reported, TaskUsage):
        return dataclasses.replace(reported, model=reported.model or model_id, **common)
    if isinstance(reported, dict):
        return TaskUsage(
            model=reported.get("model") or model_id, input_tokens=reported.get("input_tokens"),
            output_tokens=reported.get("output_tokens"), cache_read_tokens=reported.get("cache_read_tokens"),
            cache_creation_tokens=reported.get("cache_creation_tokens"), llm_calls=int(reported.get("llm_calls", 1)),
            usage_source=reported.get("usage_source", "not_available"), **common)
    return TaskUsage(model=model_id, llm_calls=1, usage_source="not_available", **common)


def _execute_model_step(
    adapter: Any,
    context: ContextPackage,
    workflow: str,
    *,
    task_id: Optional[str],
    start_time: float,
    max_input_tokens: int,
    allow_incomplete: bool,
    state_store: StateStore,
    executor_kwargs: dict[str, Any],
) -> WorkflowOutput:
    tid = task_id or f"TASK_{workflow.upper()}_MODEL"
    identity = adapter_identity(adapter)
    model_id = identity.get("model_id") or getattr(adapter, "model_id", None)
    schema = output_schema(workflow)
    system_prompt = build_system_prompt(workflow, context.skill_instructions, schema)
    user_prompt = context.formatted_user_prompt
    request_tokens, token_basis = adapter_token_count(adapter, system_prompt, user_prompt)
    provenance = [{"object_id": o["object_id"], "revision": o["revision"]} for o in context.objects]
    omitted = tuple(f"{o.get('object_id')}@{o.get('revision')}" for o in context.omitted_items)
    ctx_status = ContextStatus(context.context_status)

    def elapsed() -> int:
        return int((time.time() - start_time) * 1000)

    def no_call_usage() -> TaskUsage:
        return TaskUsage(workflow=workflow, task_id=tid, model=model_id, llm_calls=0,
                         context_objects=len(context.objects), elapsed_ms=elapsed(), usage_source="not_available")

    def build(status: str, result: dict[str, Any], usage: TaskUsage, axes: Status, notes: list[str],
              **meta: Any) -> WorkflowOutput:
        return WorkflowOutput(
            workflow=workflow, task_id=tid, project_id=state_store.project_id, status=status, result=result,
            usage=usage, provenance_refs=provenance, notes=notes, status_axes=axes,
            metadata={"model_backed": True, "model_id": model_id, "model": identity,
                      "request_tokens": request_tokens, "token_count_basis": token_basis, **meta})

    # Never send a request that does not fit, nor one missing must-have evidence, whatever allow_incomplete says
    # (I4). Relevant-but-omitted evidence (PARTIAL_T1) needs allow_incomplete; omitted background (PARTIAL_T2) does not.
    overflow = request_tokens > max_input_tokens
    insufficient = ctx_status is ContextStatus.INSUFFICIENT
    needs_consent = context.coverage == "PARTIAL_T1" and not allow_incomplete
    if overflow or insufficient or needs_consent:
        if overflow:
            reason = (f"assembled request is {request_tokens} tokens ({token_basis}) against max_input_tokens="
                      f"{max_input_tokens}")
        elif insufficient:
            reason = "must-have evidence (the question, the claims under assessment or the evidence linked to them) does not fit the budget"
        else:
            reason = "relevant evidence was omitted for budget and allow_incomplete is False"
        result = {"refused": True, "reason": reason, "request_tokens": request_tokens,
                  "max_input_tokens": max_input_tokens, "token_count_basis": token_basis,
                  "omitted_items": context.omitted_items, "continuation_plan": context.continuation_plan}
        axes = Status(ExecutionStatus.NOT_RUN, ContextStatus.INSUFFICIENT if (overflow or insufficient) else ctx_status,
                      AssessmentStatus.NOT_ASSESSED, Applicability.APPLICABLE, reason="budget_refused", omissions=omitted)
        return build("INCOMPLETE_CONTEXT", result, no_call_usage(), axes,
                     ["Model call refused: the request would exceed the input budget or rest on omitted evidence."])

    call_started = time.time()
    raw: Any = None
    reported_usage: Any = None
    failure: Optional[tuple[str, str, str]] = None  # (legacy status, execution reason, message)
    try:
        returned = adapter.generate(system_prompt, user_prompt, expected_schema=schema, temperature=0.0)
        if isinstance(returned, tuple) and len(returned) == 2:
            raw, reported_usage = returned
        else:
            raw = returned
    except ModelRefusal as exc:
        reported_usage, raw = exc.usage, exc.partial_output
        failure = ("FAILED", "refusal", f"refused ({exc.category or 'no category'}): {exc}")
    except ModelOutputTruncated as exc:
        reported_usage, raw = exc.usage, exc.partial_output
        failure = ("INVALID_OUTPUT", "invalid_output", f"truncated: {exc}")
    except Exception as exc:  # noqa: BLE001  (an adapter may raise anything; the run is recorded as FAILED)
        reported_usage = getattr(exc, "usage", None)
        failure = ("FAILED", "exception", f"{type(exc).__name__}: {exc}")
    usage = _normalize_usage(reported_usage, workflow=workflow, task_id=tid, context=context, model_id=model_id,
                             elapsed_ms=int((time.time() - call_started) * 1000))

    if failure is not None:
        legacy, reason, message = failure
        result = {"error": message}
        if isinstance(raw, str) and raw:
            result["partial_output"] = raw[:_MAX_RAW_OUTPUT_CHARS]
        axes = Status(ExecutionStatus.FAILED, ctx_status, AssessmentStatus.NOT_ASSESSED, Applicability.APPLICABLE,
                      reason=reason, omissions=omitted)
        return build(legacy, result, usage, axes, [f"Model call did not produce a usable answer: {message}"])

    outcome = validate_model_output(workflow, raw, context, schema)
    if not outcome.ok:
        raw_text = raw if isinstance(raw, str) else json.dumps(raw, default=str)
        result = {"validation_errors": outcome.errors, "raw_output": raw_text[:_MAX_RAW_OUTPUT_CHARS]}
        axes = Status(ExecutionStatus.FAILED, ctx_status, AssessmentStatus.NOT_ASSESSED, Applicability.APPLICABLE,
                      reason="invalid_output", omissions=omitted)
        return build("INVALID_OUTPUT", result, usage, axes,
                     [f"Model answer rejected ({len(outcome.errors)} validation error(s)); it is stored but not usable."])

    gates = _EXECUTORS[workflow](context, task_id=f"{tid}_GATES", **executor_kwargs)
    gate = _gate_summary(gates)
    conflicts = detect_gate_conflicts(outcome.result or {}, gate)
    result = dict(outcome.result or {})
    result["deterministic_gates"] = gate
    result["gate_conflicts"] = conflicts
    result["model_generated"] = True
    result["validation"] = {"refs_checked": outcome.refs_checked, "spans_verified": outcome.spans_verified}
    assessment = apply_caps(AssessmentStatus.PROVISIONAL, context=ctx_status, generated_by=f"model:{model_id}")
    axes = Status(ExecutionStatus.SUCCEEDED, ctx_status, assessment, Applicability.APPLICABLE, omissions=omitted)
    notes = ["Model-backed assessment: well-formed and cites only evidence present in the request, but not adjudicated, so PROVISIONAL."]
    if conflicts:
        notes.append("The answer conflicts with a deterministic evidence gate; see result.gate_conflicts.")
    return build("REQUIRES_VERIFICATION", result, usage, axes, notes)


# ---------------------------------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------------------------------

def _persist_output(store: StateStore, output: WorkflowOutput, cache_dir: Path, cache_file: Path) -> None:
    """Store the output in the content-addressed store (failure is an error) and, when the status allows,
    in the disposable JSON cache (failure is recorded, not fatal)."""
    try:
        out_bytes = json.dumps(output.to_dict(), indent=2, sort_keys=True).encode("utf-8")
        artifact_sha = store.artifacts.put(out_bytes)
    except Exception as exc:  # noqa: BLE001
        raise WorkflowPersistenceError(
            f"could not store the {output.workflow} output (status {output.status}): {type(exc).__name__}: {exc}") from exc
    output.metadata["artifact_sha256"] = artifact_sha
    output.metadata["artifact_id"] = f"WFO_{output.workflow.upper()}_{artifact_sha[:12]}"

    if output.status not in _CACHEABLE_STATUSES:
        return
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(cache_dir), prefix="wfc_", suffix=".tmp")
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(output.to_dict(), indent=2, sort_keys=True))
        os.replace(tmp_path, str(cache_file))
    except Exception as exc:  # noqa: BLE001
        output.metadata["cache_write_error"] = f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------------------------------

def run_workflow(
    arg1: Any = None,
    arg2: Any = None,
    *,
    store: Optional[StateStore] = None,
    workflow: Optional[str] = None,
    research_question_id: Optional[str] = None,
    focus_claims: Optional[Sequence[str]] = None,
    max_input_tokens: int = 12000,
    fulltext_policy: str = "on_demand",
    fulltext_sources: Optional[Sequence[str]] = None,
    history_policy: str = "relevant_changes_only",
    discipline_profile: Optional[str] = None,
    discipline: Optional[str] = None,
    reuse_cached: bool = True,
    task_id: Optional[str] = None,
    extra_kwargs: Optional[dict[str, Any]] = None,
    upstream_artifacts: Optional[list[Any]] = None,
    model_adapter: Optional[Any] = None,
    allow_incomplete: bool = False,
    **kwargs: Any,
) -> WorkflowOutput:
    """Run an individual research workflow with selective context, optional artifact reuse, and model adapter support.

    With `model_adapter`, the workflow is executed by the model (see module docstring); `allow_incomplete` lets a
    request go out when low-priority evidence was omitted for budget, but never when the request itself exceeds
    `max_input_tokens`.
    """
    start_time = time.time()
    if isinstance(arg1, StateStore):
        state_store = arg1
        wf_name = arg2 if arg2 is not None else workflow
    elif isinstance(arg1, str):
        wf_name = arg1
        state_store = arg2 if isinstance(arg2, StateStore) else store
    else:
        state_store = store
        wf_name = workflow

    if state_store is None:
        raise ValueError("StateStore must be provided to run_workflow")
    if wf_name is None:
        raise ValueError("Workflow name must be provided to run_workflow")

    canonical_wf = wf_name.strip().lower()
    if canonical_wf not in _EXECUTORS:
        available = list(_EXECUTORS.keys())
        raise KeyError(f"Unknown workflow '{wf_name}'. Available: {available}")

    profile = discipline_profile or discipline
    combined_extra = dict(extra_kwargs or {})
    if profile:
        combined_extra["discipline_profile"] = profile
    if upstream_artifacts:
        combined_extra["upstream_artifacts"] = upstream_artifacts
    if model_adapter is not None:
        combined_extra["model_adapter"] = model_adapter
    combined_extra.update(kwargs)

    adapter = None
    for key in _ADAPTER_KEYS:
        adapter = combined_extra.pop(key, None) or adapter
    executor_kwargs = dict(combined_extra)
    if profile and canonical_wf == "methods":
        executor_kwargs.setdefault("discipline_profile", profile)

    # Build selective context package
    context = build_context(
        state_store,
        canonical_wf,
        research_question_id=research_question_id,
        focus_claims=focus_claims,
        max_input_tokens=max_input_tokens,
        fulltext_policy=fulltext_policy,  # type: ignore
        fulltext_sources=fulltext_sources,
        history_policy=history_policy,  # type: ignore
        discipline_profile=profile,
    )

    fingerprint_config = dict(combined_extra)
    if adapter is not None:
        fingerprint_config["model_adapter"] = adapter
        fingerprint_config["allow_incomplete"] = allow_incomplete
    fingerprint = _compute_input_fingerprint(canonical_wf, context, fingerprint_config)
    cache_dir = _get_cache_dir(state_store)
    cache_file = cache_dir / f"{canonical_wf}_{fingerprint[:16]}.json"

    tracker = UsageTracker(state_store)

    # 1. Check Artifact Reuse / Caching (a cache hit does NOT fabricate provider cache tokens)
    if reuse_cached and cache_file.exists():
        try:
            cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
            reused_usage = TaskUsage(
                workflow=canonical_wf,
                task_id=task_id or f"TASK_{canonical_wf.upper()}_REUSED",
                model=None,
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=None,  # Local JSON cache reuse is NOT provider billed cache read tokens!
                llm_calls=0,
                context_objects=len(context.objects),
                reused_artifacts=1,
                elapsed_ms=1,
                usage_source="offline_cache",
            )
            tracker.record_task(reused_usage)

            original_status = cached_data.get("status")
            try:
                original_axes = axes_from_legacy(original_status) if original_status else None
            except ValueError:
                original_axes = None
            return WorkflowOutput(
                workflow=canonical_wf,
                task_id=reused_usage.task_id,
                project_id=state_store.project_id,
                status="REUSED",
                result=cached_data.get("result", {}),
                usage=reused_usage,
                input_fingerprint=fingerprint,
                provenance_refs=cached_data.get("provenance_refs", []),
                notes=["Reused valid cached workflow artifact; input revisions and configuration unchanged."],
                metadata={"reused": True, "artifact_sha256": cached_data.get("metadata", {}).get("artifact_sha256"),
                          "original_status": original_status},
                status_axes=original_axes,
            )
        except Exception:
            pass  # Fall back to live execution if cache read fails

    # 2. Execute selected workflow: model-backed, or the deterministic executor
    if adapter is not None:
        output = _execute_model_step(
            adapter, context, canonical_wf, task_id=task_id, start_time=start_time,
            max_input_tokens=max_input_tokens, allow_incomplete=allow_incomplete, state_store=state_store,
            executor_kwargs=executor_kwargs)
    else:
        output = _EXECUTORS[canonical_wf](context, task_id=task_id, **executor_kwargs)
    output.input_fingerprint = fingerprint

    # 3. Persist (CAS is required; the JSON cache is disposable) and record instrumentation
    _persist_output(state_store, output, cache_dir, cache_file)
    tracker.record_task(output.usage)
    return output


def run_workflow_sequence(
    store: StateStore,
    sequence: Sequence[str],
    *,
    research_question_id: Optional[str] = None,
    focus_claims: Optional[Sequence[str]] = None,
    max_input_tokens: int = 12000,
    discipline_profile: Optional[str] = None,
    reuse_cached: bool = True,
    **kwargs: Any,
) -> list[WorkflowOutput]:
    """Execute a sequence of workflows with structured artifact handoffs (Results -> Discussion -> Contribution)."""
    results: list[WorkflowOutput] = []
    for wf in sequence:
        res = run_workflow(
            store,
            wf,
            research_question_id=research_question_id,
            focus_claims=focus_claims,
            max_input_tokens=max_input_tokens,
            discipline_profile=discipline_profile,
            reuse_cached=reuse_cached,
            upstream_artifacts=results,
            **kwargs,
        )
        results.append(res)
    return results


def check_result_change_impact(store: StateStore, result_id: str, to_revision: Optional[int] = None) -> dict[str, Any]:
    """Trace DAG impact of changing a Result revision to identify affected claims and downstream workflows."""
    with store.read() as conn:
        head_res = store.get_object(result_id, conn=conn)
        if not head_res:
            return {"result_id": result_id, "status": "NOT_FOUND"}

        head_rev = head_res["head_revision"]
        rev_rows = conn.execute("SELECT revision FROM objects WHERE object_id=? ORDER BY revision ASC", (result_id,)).fetchall()
        revisions = [r[0] for r in rev_rows]

        # Find claims directly or indirectly connected to this result
        affected_claims = []
        all_claims = conn.execute("SELECT object_id FROM object_heads WHERE object_type='Claim'").fetchall()
        for (cid,) in all_claims:
            c_obj = store.get_object(cid, conn=conn)
            deps = c_obj.get("content", {}).get("depends_on", [])
            edges = store.edges_from(conn, cid, c_obj["head_revision"])
            edge_targets = {e["target_id"] for e in edges}
            if result_id in deps or result_id in edge_targets:
                affected_claims.append({
                    "claim_id": cid,
                    "head_revision": c_obj["head_revision"],
                    "claim_tier": c_obj.get("content", {}).get("claim_tier"),
                })

        claim_ids = [c["claim_id"] for c in affected_claims]
        return {
            "result_id": result_id,
            "head_revision": head_rev,
            "total_revisions": len(revisions),
            "is_revised": len(revisions) > 1,
            "affected_claims": claim_ids,
            "affected_claim_objects": affected_claims,
            "affected_claims_count": len(affected_claims),
            "requires_workflows": ["results", "discussion", "contribution"] if len(revisions) > 1 else [],
        }
