"""Phase 1 (plan v3): the model-backed execution path, end to end, without a live API.

Covers: request construction (skill, schema, evidence), budget refusal, answer validation (schema, citations,
quotes), usage fidelity, failure modes, persistence and caching, the Anthropic adapter on a fake client, and the
quote-verification primitive.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from research_harness.state.store import StateStore
from research_harness.workflows import adapters as adapters_mod
from research_harness.workflows import dispatcher as dispatcher_mod
from research_harness.workflows import quotes
from research_harness.workflows import registry as reg_mod
from research_harness.workflows.adapters import (
    AnthropicMessagesAdapter,
    ModelOutputTruncated,
    ModelRefusal,
    ScriptedModelAdapter,
    adapter_identity,
    adapter_token_count,
)
from research_harness.workflows.context import build_context
from research_harness.workflows.contracts import TaskUsage
from research_harness.workflows.dispatcher import WorkflowPersistenceError, run_workflow
from research_harness.workflows.instrumentation import UsageTracker
from research_harness.workflows.model_io import (
    RESULT_TYPES,
    build_system_prompt,
    iter_evidence_refs,
    output_schema,
    parse_model_output,
    schema_digest,
    validate_model_output,
)
from research_harness.workflows.status import AssessmentStatus, ContextStatus, ExecutionStatus

WORKFLOWS = sorted(RESULT_TYPES)


# --------------------------------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------------------------------

@pytest.fixture
def proj(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_MODEL")
    with store.write_tx(token=token) as tx:
        add = lambda oid, t, c, e=(): tx.add_revision(object_id=oid, object_type=t, content=c, origin={}, edges=list(e))  # noqa: E731
        add("RQ_TEST", "ResearchQuestion", {"text": "Does board independence reduce restatements?"})
        add("SRC_A", "Source", {"citation_key": "Klein2002", "abstract": "Audit committee independence.",
                                "full_text": "Section 4. We find a negative association."},
            [("depends_on", "RQ_TEST", 1, {})])
        add("EP_A", "EvidencePassage",
            {"locator": "Klein2002 p.389", "quote": "Board independence is negatively related to abnormal accruals."},
            [("derived_from", "SRC_A", 1, {}), ("depends_on", "RQ_TEST", 1, {})])
        add("RES_A", "Result", {"summary": "Coefficient on independence is -0.12 (p<0.01)."}, [("depends_on", "RQ_TEST", 1, {})])
        add("CLAIM_A", "Claim", {"text": "Board independence reduces restatements.", "claim_tier": "causal"},
            [("depends_on", "RQ_TEST", 1, {}), ("depends_on", "RES_A", 1, {}), ("supports", "EP_A", 1, {})])
    return store, token


def minimal_value(schema: dict):
    if "anyOf" in schema:
        return None
    return {"array": [], "object": {}, "string": "x", "boolean": True, "integer": 0, "number": 0.0, "null": None}[schema.get("type", "object")]


def valid_answer(workflow: str, refs=None, **overrides) -> dict:
    schema = output_schema(workflow)
    answer = {name: minimal_value(schema["properties"][name]) for name in schema["required"]}
    answer["evidence_refs"] = refs if refs is not None else [{"object_id": "RQ_TEST", "revision": 1}]
    answer.update(overrides)
    return answer


def run(proj, wf, adapter, **kw):
    kw.setdefault("research_question_id", "RQ_TEST")
    kw.setdefault("reuse_cached", False)
    return run_workflow(wf, proj[0], model_adapter=adapter, **kw)


class StubAdapter:
    """Adapter whose behaviour a test fully controls."""
    model_id = "stub-model"

    def __init__(self, returned=None, usage=None, raises=None):
        self.returned, self.usage, self.raises, self.calls = returned, usage, raises, []

    def describe(self):
        return {"provider": "stub", "model_id": self.model_id, "variant": json.dumps(self.returned, default=str)[:40]}

    def generate(self, system_prompt, user_prompt, *, expected_schema=None, temperature=0.0):
        self.calls.append((system_prompt, user_prompt, expected_schema))
        if self.raises:
            raise self.raises
        return self.returned, self.usage


# --------------------------------------------------------------------------------------------------
# quote verification primitive
# --------------------------------------------------------------------------------------------------

def test_find_quote_ignores_whitespace_differences_and_maps_offsets():
    text = "Intro.\n\nBoard   independence\nis negatively related to accruals.  Next."
    span = quotes.find_quote(text, "Board independence is negatively related")
    assert span is not None
    start, end = span
    assert text[start:end].split() == "Board independence is negatively related".split()
    assert quotes.find_quote(text, "board independence") is None  # case matters
    assert quotes.find_quote(text, "") is None


def test_verify_span_cases():
    text = "alpha beta gamma"
    assert quotes.verify_span(text, None, None, "beta gamma").ok
    assert quotes.verify_span(text, 6, 10, "beta").ok
    assert not quotes.verify_span(text, 6, 10, "gamma").ok
    assert not quotes.verify_span(text, 0, 99, None).ok
    assert not quotes.verify_span(text, 3, None, "x").ok
    assert not quotes.verify_span(None, None, None, "x").ok
    assert not quotes.verify_span(text, None, None, None).ok


# --------------------------------------------------------------------------------------------------
# schema / prompts / validation primitives
# --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("wf", WORKFLOWS)
def test_schema_requires_result_fields_and_evidence_refs(wf):
    schema = output_schema(wf)
    assert "evidence_refs" in schema["required"]
    assert set(f for f in RESULT_TYPES[wf].__dataclass_fields__ if f not in
               {"is_preliminary", "reopens_contribution_workflow", "profile_checklist_results"}) <= set(schema["required"])
    assert schema_digest(wf) == schema_digest(wf)


def test_schema_digest_differs_between_workflows():
    assert len({schema_digest(w) for w in WORKFLOWS}) == len(WORKFLOWS)


def test_system_prompt_carries_selected_skill_schema_and_rules_only():
    prompt = build_system_prompt("methods", reg_mod.load_skill_instructions("methods"))
    assert reg_mod.load_skill_instructions("methods")[:200] in prompt
    for other in WORKFLOWS:
        if other != "methods":
            assert reg_mod.load_skill_instructions(other)[:200] not in prompt
    assert "evidence_refs" in prompt and "do not estimate character offsets" in prompt


def test_parse_model_output_forms():
    assert parse_model_output({"a": 1})[0] == {"a": 1}
    assert parse_model_output('{"a": 1}')[0] == {"a": 1}
    assert parse_model_output('```json\n{"a": 1}\n```')[0] == {"a": 1}
    assert parse_model_output("not json")[0] is None
    assert "must be an object" in parse_model_output("[1, 2]")[1]
    assert parse_model_output(42)[0] is None


def test_iter_evidence_refs_finds_nested_refs():
    doc = {"evidence_refs": [{"object_id": "A", "revision": 1}],
           "items": [{"x": {"evidence_basis": [{"object_id": "B", "revision": 2}]}}]}
    assert sorted(r["object_id"] for _, r in iter_evidence_refs(doc)) == ["A", "B"]


# --------------------------------------------------------------------------------------------------
# request construction
# --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("wf", WORKFLOWS)
def test_request_carries_selected_skill_schema_and_evidence(proj, wf):
    adapter = ScriptedModelAdapter(default_response=valid_answer(wf))
    run(proj, wf, adapter)
    req = adapter.recorded_requests[0]
    assert reg_mod.load_skill_instructions(wf)[:200] in req.system_prompt
    for other in WORKFLOWS:
        if other != wf:
            assert reg_mod.load_skill_instructions(other)[:200] not in req.system_prompt + req.user_prompt
    assert req.expected_schema == output_schema(wf)
    assert "Operational Skill Instructions" not in req.user_prompt  # the skill is in the system prompt only
    assert "RQ_TEST" in req.user_prompt


def test_evidence_text_and_locator_reach_the_model(proj):
    adapter = ScriptedModelAdapter(default_response=valid_answer("contribution"))
    run(proj, "contribution", adapter)
    user = adapter.recorded_requests[0].user_prompt
    assert "negatively related to abnormal accruals" in user and "Klein2002 p.389" in user


# --------------------------------------------------------------------------------------------------
# accepted answers
# --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("wf", WORKFLOWS)
def test_valid_answer_is_provisional_never_verified(proj, wf):
    out = run(proj, wf, ScriptedModelAdapter(default_response=valid_answer(wf)))
    assert out.status == "REQUIRES_VERIFICATION"
    assert out.axes.execution is ExecutionStatus.SUCCEEDED
    assert out.axes.assessment is AssessmentStatus.PROVISIONAL
    assert out.result["model_generated"] is True and "deterministic_gates" in out.result
    assert out.usage.llm_calls == 1 and out.usage.usage_source == "scripted"


def test_valid_answer_is_persisted_and_retrievable(proj):
    store, _ = proj
    out = run(proj, "contribution", ScriptedModelAdapter(default_response=valid_answer("contribution")))
    sha = out.metadata["artifact_sha256"]
    assert store.artifacts.has(sha) and store.artifacts.verify(sha)
    stored = json.loads(store.artifacts.get_bytes(sha))
    assert stored["workflow"] == "contribution" and stored["status"] == "REQUIRES_VERIFICATION"
    assert stored["status_axes"]["assessment"] == "PROVISIONAL"
    records = UsageTracker(store).get_records("contribution")
    assert records and records[-1]["llm_calls"] == 1


def test_json_string_and_fenced_answers_are_accepted(proj):
    answer = valid_answer("methods")
    for raw in (json.dumps(answer), "```json\n" + json.dumps(answer) + "\n```"):
        out = run(proj, "methods", ScriptedModelAdapter(default_response=raw))
        assert out.status == "REQUIRES_VERIFICATION"


def test_quote_is_verified_and_offsets_are_computed_not_trusted(proj):
    ref = {"object_id": "EP_A", "revision": 1, "quote": "Board independence is negatively related"}
    out = run(proj, "contribution", ScriptedModelAdapter(default_response=valid_answer("contribution", refs=[ref])))
    assert out.status == "REQUIRES_VERIFICATION"
    cited = out.result["evidence_refs"][0]
    assert (cited["char_start"], cited["char_end"], cited["quote_verified"]) == (0, 40, True)
    assert out.result["validation"] == {"refs_checked": 1, "spans_verified": 1}


def test_gate_conflict_is_recorded_and_status_stays_provisional(proj):
    answer = valid_answer("contribution", identification_status="IDENTIFIED_BY_VERIFIED_DESIGN")
    out = run(proj, "contribution", ScriptedModelAdapter(default_response=answer), focus_claims=["CLAIM_A"])
    assert out.status == "REQUIRES_VERIFICATION"
    assert out.result["gate_conflicts"] and "identification_status" in out.result["gate_conflicts"][0]
    assert out.axes.assessment is AssessmentStatus.PROVISIONAL


# --------------------------------------------------------------------------------------------------
# rejected answers
# --------------------------------------------------------------------------------------------------

def _rejected(proj, answer, wf="contribution"):
    adapter = ScriptedModelAdapter(default_response=answer)
    out = run(proj, wf, adapter)
    assert out.status == "INVALID_OUTPUT"
    assert out.axes.execution is ExecutionStatus.FAILED and out.axes.reason == "invalid_output"
    assert out.axes.assessment is AssessmentStatus.NOT_ASSESSED
    assert out.usage.llm_calls == 1  # the call happened and is accounted for
    return out


def test_missing_required_fields_rejected(proj):
    out = _rejected(proj, {"assessment": "REQUIRES_VERIFICATION", "evidence_refs": []})
    assert any(e.startswith("schema:") for e in out.result["validation_errors"])


def test_non_json_output_rejected_with_raw_text_kept(proj):
    out = _rejected(proj, "I think the contribution is novel and causal.")
    assert "not valid JSON" in out.result["validation_errors"][0]
    assert out.result["raw_output"].startswith("I think")


def test_invented_citation_rejected(proj):
    out = _rejected(proj, valid_answer("contribution", refs=[{"object_id": "EP_FAKE", "revision": 1}]))
    assert any("EP_FAKE@1 is not in the request" in e for e in out.result["validation_errors"])


def test_citation_of_wrong_revision_rejected(proj):
    out = _rejected(proj, valid_answer("contribution", refs=[{"object_id": "EP_A", "revision": 2}]))
    assert any("EP_A@2" in e for e in out.result["validation_errors"])


def test_fabricated_quote_rejected(proj):
    ref = {"object_id": "EP_A", "revision": 1, "quote": "Independence eliminates all restatements"}
    out = _rejected(proj, valid_answer("contribution", refs=[ref]))
    assert any(e.startswith("quote:") for e in out.result["validation_errors"])


def test_wrong_offsets_rejected_even_with_a_real_quote(proj):
    ref = {"object_id": "EP_A", "revision": 1, "quote": "Board independence", "char_start": 10, "char_end": 28}
    out = _rejected(proj, valid_answer("contribution", refs=[ref]))
    assert any("differs from the quote" in e for e in out.result["validation_errors"])


def test_nested_invented_citation_rejected(proj):
    answer = valid_answer("contribution", candidate_contributions=[{"claim": "x", "evidence_refs": [{"object_id": "NOPE", "revision": 1}]}])
    out = _rejected(proj, answer)
    assert any("NOPE@1" in e for e in out.result["validation_errors"])


# --------------------------------------------------------------------------------------------------
# failures
# --------------------------------------------------------------------------------------------------

def test_refusal_is_recorded_with_its_usage_and_never_cached(proj):
    usage = TaskUsage(workflow="x", task_id="x", model="stub-model", input_tokens=900, output_tokens=0, llm_calls=1,
                      usage_source="provider")
    adapter = StubAdapter(raises=ModelRefusal("declined", category="cyber", usage=usage))
    out = run(proj, "contribution", adapter, reuse_cached=True)
    assert out.status == "FAILED" and out.axes.reason == "refusal"
    assert "cyber" in out.result["error"]
    assert out.usage.input_tokens == 900 and out.usage.usage_source == "provider"
    run(proj, "contribution", adapter, reuse_cached=True)
    assert len(adapter.calls) == 2  # not served from cache


def test_truncated_output_is_invalid_not_complete(proj):
    adapter = StubAdapter(raises=ModelOutputTruncated("hit max", partial_output='{"candidate_contribut'))
    out = run(proj, "contribution", adapter)
    assert out.status == "INVALID_OUTPUT"
    assert out.result["error"].startswith("truncated") and out.result["partial_output"].startswith('{"candidate')


def test_adapter_exception_is_recorded_as_failed_not_raised(proj):
    out = run(proj, "contribution", StubAdapter(raises=RuntimeError("network down")))
    assert out.status == "FAILED" and out.axes.reason == "exception"
    assert out.result["error"] == "RuntimeError: network down"
    assert out.metadata["artifact_sha256"]


def test_persistence_failure_is_an_error_not_silent(proj, monkeypatch):
    store, _ = proj
    monkeypatch.setattr(store.artifacts, "put", lambda data: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(WorkflowPersistenceError, match="disk full"):
        run(proj, "contribution", ScriptedModelAdapter(default_response=valid_answer("contribution")))


def test_run_registration_failure_is_an_error_not_silent(proj, monkeypatch):
    monkeypatch.setattr(dispatcher_mod.workflow_runs, "append_run", lambda *a, **k: (_ for _ in ()).throw(OSError("db locked")))
    with pytest.raises(WorkflowPersistenceError, match="could not register.*db locked"):
        run(proj, "contribution", ScriptedModelAdapter(default_response=valid_answer("contribution")))


# --------------------------------------------------------------------------------------------------
# usage fidelity
# --------------------------------------------------------------------------------------------------

def test_scripted_token_costs_recorded_verbatim(proj):
    adapter = ScriptedModelAdapter(default_response=valid_answer("contribution"), input_token_cost=777, output_token_cost=11)
    out = run(proj, "contribution", adapter)
    assert (out.usage.input_tokens, out.usage.output_tokens) == (777, 11)
    assert out.usage.workflow == "contribution" and out.usage.model == "mock-reasoning-model-v1"


def test_provider_usage_including_cache_figures_copied_verbatim(proj):
    reported = TaskUsage(workflow="w", task_id="t", model="stub-model", input_tokens=1234, output_tokens=56,
                         cache_read_tokens=1000, cache_creation_tokens=7, llm_calls=1, usage_source="provider")
    out = run(proj, "contribution", StubAdapter(returned=valid_answer("contribution"), usage=reported))
    u = out.usage
    assert (u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_creation_tokens, u.usage_source) == (
        1234, 56, 1000, 7, "provider")
    assert u.workflow == "contribution" and u.context_objects > 0


def test_missing_usage_is_not_fabricated(proj):
    out = run(proj, "contribution", StubAdapter(returned=valid_answer("contribution"), usage=None))
    u = out.usage
    assert (u.input_tokens, u.output_tokens, u.cache_read_tokens) == (None, None, None)
    assert u.usage_source == "not_available" and u.llm_calls == 1


def test_bare_result_without_usage_tuple_is_tolerated(proj):
    class Bare(StubAdapter):
        def generate(self, *a, **k):
            return valid_answer("contribution")
    out = run(proj, "contribution", Bare())
    assert out.status == "REQUIRES_VERIFICATION" and out.usage.input_tokens is None


# --------------------------------------------------------------------------------------------------
# budget
# --------------------------------------------------------------------------------------------------

def test_request_over_budget_is_not_sent(proj):
    adapter = ScriptedModelAdapter(default_response=valid_answer("contribution"))
    out = run(proj, "contribution", adapter, max_input_tokens=200)
    assert adapter.recorded_requests == []
    assert out.status == "INCOMPLETE_CONTEXT"
    assert out.axes.execution is ExecutionStatus.NOT_RUN and out.axes.reason == "budget_refused"
    assert out.usage.llm_calls == 0 and out.usage.input_tokens is None
    assert out.result["request_tokens"] > 200


def test_over_budget_is_refused_even_with_allow_incomplete(proj):
    adapter = ScriptedModelAdapter(default_response=valid_answer("contribution"))
    out = run(proj, "contribution", adapter, max_input_tokens=200, allow_incomplete=True)
    assert adapter.recorded_requests == [] and out.status == "INCOMPLETE_CONTEXT"


def _project_with_omitted_evidence(tmp_path):
    store, token = StateStore.create(tmp_path / "home2", "PRJ_OMIT")
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id="RQ_TEST", object_type="ResearchQuestion", content={"text": "Q?"}, origin={}, edges=[])
        tx.add_revision(object_id="EP_SMALL", object_type="EvidencePassage", content={"locator": "p.1", "quote": "Short."},
                        origin={}, edges=[("depends_on", "RQ_TEST", 1, {})])
        tx.add_revision(object_id="EP_HUGE", object_type="EvidencePassage",
                        content={"locator": "p.2", "quote": "word " * 30000}, origin={}, edges=[("depends_on", "RQ_TEST", 1, {})])
    return store


def test_incomplete_selection_refused_unless_allowed_and_then_marked_partial(tmp_path):
    store = _project_with_omitted_evidence(tmp_path)
    ctx = build_context(store, "contribution", research_question_id="RQ_TEST", max_input_tokens=6000)
    assert ctx.budget_status == "INCOMPLETE_EXCEEDED" and ctx.omitted_items  # EP_HUGE was left out

    refused = ScriptedModelAdapter(default_response=valid_answer("contribution"))
    out = run_workflow("contribution", store, research_question_id="RQ_TEST", model_adapter=refused,
                       max_input_tokens=6000, reuse_cached=False)
    assert refused.recorded_requests == [] and out.status == "INCOMPLETE_CONTEXT"
    assert out.axes.context is ContextStatus.PARTIAL and out.axes.omissions

    allowed = ScriptedModelAdapter(default_response=valid_answer("contribution"))
    out = run_workflow("contribution", store, research_question_id="RQ_TEST", model_adapter=allowed,
                       max_input_tokens=6000, allow_incomplete=True, reuse_cached=False)
    assert len(allowed.recorded_requests) == 1
    assert out.status == "REQUIRES_VERIFICATION"
    assert out.axes.context is ContextStatus.PARTIAL and out.axes.assessment is AssessmentStatus.PROVISIONAL
    assert any("EP_HUGE" in o for o in out.axes.omissions)


# --------------------------------------------------------------------------------------------------
# caching
# --------------------------------------------------------------------------------------------------

def test_valid_run_is_reused_without_calling_the_model_again(proj):
    adapter = ScriptedModelAdapter(default_response=valid_answer("contribution"))
    first = run(proj, "contribution", adapter, reuse_cached=True)
    second = run(proj, "contribution", adapter, reuse_cached=True)
    assert len(adapter.recorded_requests) == 1
    assert second.status == "REUSED" and second.usage.llm_calls == 0 and second.usage.usage_source == "offline_cache"
    assert second.metadata["original_status"] == first.status
    assert second.axes.assessment is AssessmentStatus.PROVISIONAL  # the original assessment survives the reuse


def test_invalid_output_is_never_reused(proj):
    adapter = ScriptedModelAdapter(default_response={"nope": 1})
    run(proj, "contribution", adapter, reuse_cached=True)
    run(proj, "contribution", adapter, reuse_cached=True)
    assert len(adapter.recorded_requests) == 2


def test_budget_refusal_is_never_reused(proj):
    adapter = ScriptedModelAdapter(default_response=valid_answer("contribution"))
    for _ in range(2):
        assert run(proj, "contribution", adapter, max_input_tokens=200, reuse_cached=True).status == "INCOMPLETE_CONTEXT"


def test_changing_the_scripted_answer_or_model_id_misses_the_cache(proj):
    run(proj, "contribution", ScriptedModelAdapter(default_response=valid_answer("contribution")), reuse_cached=True)
    other_answer = ScriptedModelAdapter(default_response=valid_answer("contribution", confidence_explanation="different"))
    assert run(proj, "contribution", other_answer, reuse_cached=True).status != "REUSED"
    other_model = ScriptedModelAdapter(model_id="another-model", default_response=valid_answer("contribution"))
    assert run(proj, "contribution", other_model, reuse_cached=True).status != "REUSED"


def test_equal_adapters_share_cache_entries(proj):
    a = ScriptedModelAdapter(default_response=valid_answer("contribution"))
    b = ScriptedModelAdapter(default_response=valid_answer("contribution"))
    run(proj, "contribution", a, reuse_cached=True)
    assert run(proj, "contribution", b, reuse_cached=True).status == "REUSED"
    assert b.recorded_requests == []


def test_model_and_deterministic_runs_never_share_cache_entries(proj):
    run(proj, "contribution", ScriptedModelAdapter(default_response=valid_answer("contribution")), reuse_cached=True)
    det = run_workflow("contribution", proj[0], research_question_id="RQ_TEST", reuse_cached=True)
    assert det.status != "REUSED" and not det.result.get("model_generated")


def test_allow_incomplete_is_part_of_the_cache_identity(proj):
    adapter = ScriptedModelAdapter(default_response=valid_answer("contribution"))
    run(proj, "contribution", adapter, reuse_cached=True)
    assert run(proj, "contribution", adapter, reuse_cached=True, allow_incomplete=True).status != "REUSED"


def test_adapter_identity_never_uses_repr():
    class NoDescribe:
        model_id = "m"
    ident = adapter_identity(NoDescribe())
    assert ident["provider"].endswith("NoDescribe") and ident["model_id"] == "m"
    assert adapter_identity(NoDescribe()) == adapter_identity(NoDescribe())


def test_adapter_token_count_basis(proj):
    scripted = ScriptedModelAdapter(input_token_cost=321)
    assert adapter_token_count(scripted, "sys", "user") == (321, "adapter")
    count, basis = adapter_token_count(ScriptedModelAdapter(), "sys " * 50, "user " * 50)
    assert count > 0 and basis in {"heuristic", "tiktoken"}


# --------------------------------------------------------------------------------------------------
# Anthropic adapter on a fake client
# --------------------------------------------------------------------------------------------------

class FakeMessages:
    def __init__(self, owner, beta):
        self.owner, self.beta = owner, beta

    def create(self, **kw):
        self.owner.calls.append(("beta" if self.beta else "plain", kw))
        return self.owner.response

    def count_tokens(self, **kw):
        if self.owner.count_error:
            raise RuntimeError("no network")
        self.owner.counted.append(kw)
        return SimpleNamespace(input_tokens=4321)


class FakeClient:
    def __init__(self, text='{"a": 1}', stop_reason="end_turn", stop_details=None):
        self.calls, self.counted, self.count_error = [], [], False
        self.response = SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=100, cache_read_input_tokens=50, cache_creation_input_tokens=10, output_tokens=20),
            stop_reason=stop_reason, stop_details=stop_details, model="claude-served-model")
        self.messages = FakeMessages(self, beta=False)
        self.beta = SimpleNamespace(messages=FakeMessages(self, beta=True))


def test_anthropic_adapter_requires_an_explicit_model(monkeypatch):
    monkeypatch.delenv("RESEARCH_MODEL", raising=False)
    with pytest.raises(ValueError, match="RESEARCH_MODEL"):
        AnthropicMessagesAdapter(client=FakeClient())
    monkeypatch.setenv("RESEARCH_MODEL", "claude-from-env")
    assert AnthropicMessagesAdapter(client=FakeClient()).model_id == "claude-from-env"


def test_anthropic_request_shape_plain_endpoint():
    client = FakeClient()
    adapter = AnthropicMessagesAdapter("claude-haiku-4-5", client=client)  # no documented fallback support -> plain endpoint
    text, usage = adapter.generate("SYSTEM", "USER", expected_schema={"type": "object"}, temperature=0.7)
    kind, kw = client.calls[0]
    assert kind == "plain" and kw["model"] == "claude-haiku-4-5" and kw["max_tokens"] == 16000
    assert kw["system"] == [{"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}]
    assert kw["messages"] == [{"role": "user", "content": "USER"}]
    assert kw["output_config"] == {"effort": "high"}
    assert "temperature" not in kw and "thinking" not in kw and "fallbacks" not in kw
    assert text == '{"a": 1}'


def test_anthropic_request_options_can_be_disabled():
    client = FakeClient()
    AnthropicMessagesAdapter("claude-haiku-4-5", client=client, effort=None, cache_system_prompt=False).generate("S", "U")
    kw = client.calls[0][1]
    assert "output_config" not in kw and kw["system"] == [{"type": "text", "text": "S"}]


def test_anthropic_fallbacks_use_the_beta_endpoint_for_supporting_models():
    client = FakeClient()
    AnthropicMessagesAdapter("claude-opus-5-5", client=client).generate("S", "U")
    kind, kw = client.calls[0]
    assert kind == "beta" and kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    client2 = FakeClient()
    AnthropicMessagesAdapter("claude-opus-5-5", client=client2, use_fallbacks=False).generate("S", "U")
    assert client2.calls[0][0] == "plain"


def test_anthropic_usage_mapping():
    _, usage = AnthropicMessagesAdapter("claude-haiku-4-5", client=FakeClient()).generate("S", "U")
    assert (usage.input_tokens, usage.cache_read_tokens, usage.cache_creation_tokens, usage.output_tokens) == (160, 50, 10, 20)
    assert usage.usage_source == "provider" and usage.llm_calls == 1 and usage.model == "claude-served-model"


def test_anthropic_refusal_and_truncation_raise_with_usage():
    refused = FakeClient(text="", stop_reason="refusal", stop_details=SimpleNamespace(category="bio"))
    with pytest.raises(ModelRefusal) as exc:
        AnthropicMessagesAdapter("claude-haiku-4-5", client=refused).generate("S", "U")
    assert exc.value.category == "bio" and exc.value.usage.input_tokens == 160

    cut = FakeClient(text='{"a": ', stop_reason="max_tokens")
    with pytest.raises(ModelOutputTruncated) as exc2:
        AnthropicMessagesAdapter("claude-haiku-4-5", client=cut).generate("S", "U")
    assert exc2.value.partial_output == '{"a": ' and exc2.value.usage.output_tokens == 20


def test_anthropic_count_tokens_and_failure_fallback():
    client = FakeClient()
    adapter = AnthropicMessagesAdapter("claude-haiku-4-5", client=client)
    assert adapter.count_tokens("S", "U") == 4321 and client.counted[0]["model"] == "claude-haiku-4-5"
    client.count_error = True
    assert adapter.count_tokens("S", "U") is None
    assert adapter_token_count(adapter, "S" * 40, "U" * 40)[1] in {"heuristic", "tiktoken"}


def test_anthropic_describe_is_stable_and_reflects_settings():
    a = AnthropicMessagesAdapter("claude-opus-5-5", client=FakeClient())
    b = AnthropicMessagesAdapter("claude-opus-5-5", client=FakeClient())
    assert a.describe() == b.describe() and a.describe()["temperature_sent"] is False
    assert AnthropicMessagesAdapter("claude-opus-5-5", client=FakeClient(), effort="low").describe() != a.describe()
    assert "api_key" not in json.dumps(a.describe())


def test_anthropic_adapter_through_run_workflow_end_to_end(proj):
    client = FakeClient(text="```json\n" + json.dumps(valid_answer("contribution")) + "\n```")
    adapter = AnthropicMessagesAdapter("claude-haiku-4-5", client=client)
    out = run(proj, "contribution", adapter)
    assert out.status == "REQUIRES_VERIFICATION"
    u = out.usage
    assert (u.input_tokens, u.cache_read_tokens, u.output_tokens, u.usage_source) == (160, 50, 20, "provider")
    assert out.metadata["token_count_basis"] == "adapter" and out.metadata["request_tokens"] == 4321
    assert out.metadata["model"]["provider"] == "anthropic"
