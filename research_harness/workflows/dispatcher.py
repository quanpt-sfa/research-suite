"""Selective workflow dispatcher, orchestration, and artifact caching.

Inspired by Deep Agents isolated contexts and progressive disclosure:
- Selective orchestration: runs a single workflow or specified sequence.
- Preserves token efficiency by reusing valid cached outputs when input revisions haven't changed.
- Propagates result changes to affected downstream claims and workflows.
- Records task instrumentation into the project's persistent usage log.
- Supports structured cross-workflow handoffs (Results -> Discussion -> Contribution).
- Supports injectable ModelAdapter for model-backed reasoning.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from research_harness.state.store import StateStore
from research_harness.workflows.context import build_context
from research_harness.workflows.contracts import ContextPackage, TaskUsage, WorkflowOutput
from research_harness.workflows.instrumentation import UsageTracker
from research_harness.workflows.registry import get_registry
from research_harness.workflows.skills.contribution import execute_contribution_workflow
from research_harness.workflows.skills.discussion import execute_discussion_workflow
from research_harness.workflows.skills.literature import execute_literature_workflow
from research_harness.workflows.skills.methods import execute_methods_workflow
from research_harness.workflows.skills.results import execute_results_workflow

_EXECUTORS = {
    "contribution": execute_contribution_workflow,
    "literature": execute_literature_workflow,
    "methods": execute_methods_workflow,
    "results": execute_results_workflow,
    "discussion": execute_discussion_workflow,
}


def _compute_input_fingerprint(
    workflow: str,
    context: ContextPackage,
    extra_config: Optional[dict[str, Any]] = None,
) -> str:
    """Compute deterministic SHA-256 fingerprint over all input object revisions, skills, full-text policies, and configuration."""
    safe_config: dict[str, Any] = {}
    upstream_digests: list[str] = []
    if extra_config:
        for k, v in extra_config.items():
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
    }
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_cache_dir(store: StateStore) -> Path:
    cd = store.paths.root / "workflow_cache"
    cd.mkdir(parents=True, exist_ok=True)
    return cd


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
    **kwargs: Any,
) -> WorkflowOutput:
    """Run an individual research workflow with selective context, optional artifact reuse, and model adapter support."""
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
    if model_adapter:
        combined_extra["model_adapter"] = model_adapter
    combined_extra.update(kwargs)

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

    fingerprint = _compute_input_fingerprint(canonical_wf, context, combined_extra)
    cache_dir = _get_cache_dir(state_store)
    cache_file = cache_dir / f"{canonical_wf}_{fingerprint[:16]}.json"

    tracker = UsageTracker(state_store)

    # 1. Check Artifact Reuse / Caching (Part G1: Cache hit does NOT fabricate provider cache tokens)
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
                metadata={"reused": True, "artifact_sha256": cached_data.get("metadata", {}).get("artifact_sha256")},
            )
        except Exception:
            pass  # Fall back to live execution if cache read fails

    # 2. Execute selected workflow (Check for model adapter vs deterministic executor)
    adapter = combined_extra.pop("model_adapter", None) or combined_extra.pop("adapter", None)
    if adapter is not None:
        adapter_res = adapter.generate(
            prompt=context.formatted_prompt,
            workflow=canonical_wf,
            schema_version="1.0",
        )
        adapter_usage = getattr(adapter_res, "usage", {}) or {}
        model_id = getattr(adapter, "model_id", getattr(adapter, "name", "scripted-model"))
        elapsed_ms = int((time.time() - start_time) * 1000)

        usage = TaskUsage(
            workflow=canonical_wf,
            task_id=task_id or f"TASK_{canonical_wf.upper()}_MODEL",
            model=model_id,
            input_tokens=adapter_usage.get("input_tokens", context.estimated_tokens),
            output_tokens=adapter_usage.get("output_tokens", 50),
            cache_read_tokens=adapter_usage.get("cache_read_tokens"),
            llm_calls=adapter_usage.get("llm_calls", 1),
            context_objects=len(context.objects),
            reused_artifacts=0,
            elapsed_ms=elapsed_ms,
            usage_source="provider",
        )

        res_payload = getattr(adapter_res, "result", adapter_res)
        if isinstance(res_payload, str):
            try:
                res_payload = json.loads(res_payload)
            except Exception:
                res_payload = {"raw_output": res_payload}

        output = WorkflowOutput(
            workflow=canonical_wf,
            task_id=usage.task_id,
            project_id=state_store.project_id,
            status="SUCCESS",
            result=res_payload,
            usage=usage,
            input_fingerprint=fingerprint,
            provenance_refs=[{"object_id": o["object_id"], "revision": o["revision"]} for o in context.objects],
            notes=["Model-backed assessment generated via ModelAdapter."],
            metadata={"model_backed": True, "model_id": model_id},
        )
    else:
        executor = _EXECUTORS[canonical_wf]
        call_kwargs = dict(combined_extra)
        if profile and canonical_wf == "methods":
            call_kwargs.setdefault("discipline_profile", profile)

        output = executor(context, task_id=task_id, **call_kwargs)
        output.input_fingerprint = fingerprint

    # 3. Persist Output into StateStore CAS (Acceptance Gate F)
    try:
        out_dict = output.to_dict()
        out_bytes = json.dumps(out_dict, indent=2, sort_keys=True).encode("utf-8")
        artifact_sha = state_store.artifacts.put(out_bytes)
        output.metadata["artifact_sha256"] = artifact_sha
        output.metadata["artifact_id"] = f"WFO_{canonical_wf.upper()}_{artifact_sha[:12]}"
    except Exception:
        pass

    # 4. Persist Cache Artifact atomically
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(cache_dir), prefix="wfc_", suffix=".tmp")
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(output.to_dict(), indent=2, sort_keys=True))
        os.replace(tmp_path, str(cache_file))
    except Exception:
        pass

    # 5. Record Instrumentation
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
