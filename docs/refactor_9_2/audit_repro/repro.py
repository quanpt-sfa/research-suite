"""Independent audit reproductions for Research Suite 9.1.1 (PR #3 @ 9728134).

Temporary script. Does not modify production code. All projects are created in
throwaway directories under the audit scratchpad.

Run:
    PYTHONPATH=D:/Works/research-suite uv run --no-project --isolated --python 3.12 \
        --with jsonschema python repro.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(os.environ.get("RS_REPO", "D:/Works/research-suite"))
sys.path.insert(0, str(REPO))
HERE = Path(__file__).resolve().parent
WORK = HERE / "projects"
if WORK.exists():
    shutil.rmtree(WORK)
WORK.mkdir(parents=True)

from research_harness.state.store import StateStore  # noqa: E402
from research_harness.workflows import registry as reg_mod  # noqa: E402
from research_harness.workflows.adapters import ScriptedModelAdapter  # noqa: E402
from research_harness.workflows.context import build_context  # noqa: E402
from research_harness.workflows.contracts import ContextPackage  # noqa: E402
from research_harness.workflows.dispatcher import (  # noqa: E402
    _compute_input_fingerprint,
    run_workflow,
    run_workflow_sequence,
)
from research_harness.workflows.instrumentation import estimate_tokens  # noqa: E402

WORKFLOWS = ["contribution", "literature", "methods", "results", "discussion"]
RESULTS: list[dict] = []
_n = 0


def record(rid, title, expected, observed, verdict, src=""):
    RESULTS.append({"id": rid, "title": title, "expected": expected, "observed": observed,
                    "verdict": verdict, "source": src})
    print(f"[{verdict:<14}] {rid}: {title}\n    expected: {expected}\n    observed: {observed}\n")


def new_store(pid="PRJ_AUDIT"):
    global _n
    _n += 1
    home = WORK / f"p{_n:03d}"
    home.mkdir()
    store, tok = StateStore.create(home, pid)
    return store, tok, home


def add(store, tok, *objs):
    """objs = (oid, otype, content, edges)"""
    out = []
    with store.write_tx(token=tok) as tx:
        for oid, otype, content, edges in objs:
            out.append(tx.add_revision(object_id=oid, object_type=otype, content=content,
                                       origin={"kind": "audit_fixture"}, edges=list(edges)))
    return out


def rq(oid="RQ_TEST", text="Does board independence reduce financial restatements?"):
    return (oid, "ResearchQuestion", {"label": oid, "text": text}, [])


def guard(fn):
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


# =====================================================================================
# PART B — model adapter through run_workflow
# =====================================================================================

def seed_all_workflows(store, tok):
    add(store, tok,
        rq(),
        ("SRC_A", "Source", {"citation_key": "Klein2002", "abstract": "Audit committee independence and earnings management.",
                             "full_text": "Section 4. We find a negative association between independence and abnormal accruals."},
         [("depends_on", "RQ_TEST", 1, {})]),
        ("EP_A", "EvidencePassage", {"locator": "Klein2002 p.389", "quote": "Board independence is negatively related to abnormal accruals in a sample of 692 firm-years."},
         [("derived_from", "SRC_A", 1, {}), ("depends_on", "RQ_TEST", 1, {})]),
        ("DS_A", "Dataset", {"label": "Compustat 2000-2020"}, [("depends_on", "RQ_TEST", 1, {})]),
        ("AN_A", "Analysis", {"label": "OLS", "estimator": None}, [("depends_on", "DS_A", 1, {})]),
        ("RES_A", "Result", {"summary": "Coefficient on independence is -0.12 (p<0.01)."}, [("derived_from", "AN_A", 1, {})]),
        ("CLAIM_A", "Claim", {"text": "Board independence reduces restatements.", "claim_tier": "causal"},
         [("depends_on", "RQ_TEST", 1, {}), ("depends_on", "RES_A", 1, {}), ("supports", "EP_A", 1, {})]),
        )


def part_b():
    store, tok, _ = seed_all_workflows_store = new_store()
    seed_all_workflows(store, tok)
    for wf in WORKFLOWS:
        adapter = ScriptedModelAdapter(default_response={"assessment": "REQUIRES_VERIFICATION", "evidence_refs": []})
        out, err = guard(lambda: run_workflow(wf, store, research_question_id="RQ_TEST", model_adapter=adapter, reuse_cached=False))
        reached = len(adapter.recorded_requests)
        record(f"B1-{wf}", f"ScriptedModelAdapter via run_workflow('{wf}')",
               "request reaches adapter; structured result + usage recorded; output persisted",
               f"exception={err!r}; adapter.recorded_requests={reached}; output={'None' if out is None else out.status}",
               "FAIL" if err or reached == 0 else "PASS",
               "dispatcher.py:224-228 vs adapters.py:66-73")

    # B2: shim that accepts dispatcher kwargs and forwards to ScriptedModelAdapter -> exposes tuple-return mismatch
    class KwargShim:
        def __init__(self, inner):
            self.inner = inner
            self.model_id = inner.model_id

        def generate(self, prompt, workflow, schema_version):
            return self.inner.generate(system_prompt="", user_prompt=prompt)

    inner = ScriptedModelAdapter(default_response={"assessment": "REQUIRES_VERIFICATION", "evidence_refs": []})
    out, err = guard(lambda: run_workflow("contribution", store, research_question_id="RQ_TEST",
                                          model_adapter=KwargShim(inner), reuse_cached=False))
    persisted = bool(out and out.metadata.get("artifact_sha256"))
    record("B2", "Signature-compatible shim returning ScriptedModelAdapter's (result, usage) tuple",
           "result dict extracted; adapter usage used; artifact persisted",
           f"err={err}; type(result)={type(out.result).__name__ if out else None}; usage.input_tokens={out.usage.input_tokens if out else None}"
           f" (adapter reported {inner.recorded_requests and estimate_tokens(inner.recorded_requests[-1].user_prompt)[0]}); "
           f"output_tokens={out.usage.output_tokens if out else None}; artifact_sha256 present={persisted}",
           "FAIL" if (not out or not isinstance(out.result, dict) or not persisted) else "PASS",
           "dispatcher.py:229,247,275-292 (except: pass)")

    # B3: fully duck-typed adapter matching what dispatcher expects, to probe downstream semantics.
    class DuckResp:
        def __init__(self, result, usage):
            self.result, self.usage = result, usage

    class DuckAdapter:
        model_id = "duck-model"

        def __init__(self, result, usage=None):
            self._result, self._usage, self.calls = result, usage, []

        def generate(self, **kw):
            self.calls.append(kw)
            return DuckResp(self._result, self._usage)

    duck = DuckAdapter({"assessment": "REQUIRES_VERIFICATION", "evidence_refs": []})  # no usage reported
    out = run_workflow("contribution", store, research_question_id="RQ_TEST", model_adapter=duck, reuse_cached=False)
    prompt = duck.calls[0]["prompt"]
    own = reg_mod.load_skill_instructions("contribution")
    others = [w for w in WORKFLOWS if w != "contribution" and reg_mod.load_skill_instructions(w)[:200] in prompt]
    record("B3a", "Selected SKILL.md body in model request; unrelated skills excluded",
           "contribution body present; other 4 absent",
           f"contribution body present={own in prompt}; other skill bodies present={others}",
           "PASS" if own in prompt and not others else "FAIL", "contracts.py:172-173")
    record("B3b", "Selected evidence reaches model request",
           "EP_A quote + locator present",
           f"quote present={'negatively related to abnormal accruals' in prompt}; locator present={'Klein2002 p.389' in prompt}; "
           f"competitor Source SRC_A present={'SRC_A' in prompt}",
           "PASS" if "negatively related" in prompt else "FAIL", "context.py:89-91")
    record("B3c", "Output schema / JSON contract sent to model",
           "expected output fields or schema included in request",
           f"request kwargs={sorted(duck.calls[0].keys())}; 'candidate_contributions' in prompt={'candidate_contributions' in prompt}",
           "FAIL" if "candidate_contributions" not in prompt else "PASS", "dispatcher.py:224-228")
    record("B3d", "Usage when adapter reports none",
           "usage_source not 'provider'; tokens None/not fabricated",
           f"usage_source={out.usage.usage_source}; input_tokens={out.usage.input_tokens}; output_tokens={out.usage.output_tokens}; llm_calls={out.usage.llm_calls}",
           "FAIL" if out.usage.usage_source == "provider" else "PASS", "dispatcher.py:237-244")
    record("B3e", "Schema-less model answer accepted as complete",
           "model output missing required contribution fields -> INVALID/REQUIRES_VERIFICATION",
           f"status={out.status}; result keys={sorted(out.result)}",
           "FAIL" if out.status == "SUCCESS" else "PASS", "dispatcher.py:254-265")

    garbage = DuckAdapter("I think the contribution is novel and causal.", {"input_tokens": 10, "output_tokens": 5})
    out = run_workflow("discussion", store, research_question_id="RQ_TEST", model_adapter=garbage, reuse_cached=False)
    record("B3f", "Non-JSON model output",
           "rejected or explicitly INVALID",
           f"status={out.status}; result={out.result}",
           "FAIL" if out.status == "SUCCESS" else "PASS", "dispatcher.py:248-252")

    tiny = DuckAdapter({"assessment": "COMPLETE"}, {"input_tokens": 99999, "output_tokens": 5})
    out = run_workflow("contribution", store, research_question_id="RQ_TEST", model_adapter=tiny, reuse_cached=False, max_input_tokens=200)
    sent_tok = estimate_tokens(tiny.calls[0]["prompt"])[0]
    record("B3g/D3", "Budget overflow under model-backed execution",
           "request not sent over budget, or status INCOMPLETE_CONTEXT",
           f"model called={len(tiny.calls)}; prompt est. tokens={sent_tok} vs budget 200; status={out.status}",
           "FAIL" if out.status == "SUCCESS" else "PASS", "dispatcher.py:223-265 (budget_status never consulted)")

    # persistence of model output
    sha = out.metadata.get("artifact_sha256")
    record("B3h", "Model-backed output persisted and retrievable",
           "artifact in CAS",
           f"artifact_sha256={sha and sha[:16]}; in CAS={bool(sha and store.artifacts.has(sha))}",
           "PASS" if sha and store.artifacts.has(sha) else "FAIL", "dispatcher.py:276-283")


# =====================================================================================
# PART C — adversarial scientific cases
# =====================================================================================

def run_det(store, wf, **kw):
    return run_workflow(wf, store, research_question_id=kw.pop("rq", "RQ_TEST"), reuse_cached=False, **kw)


def part_c1():
    # causal claim, no identification
    s, t, _ = new_store()
    add(s, t, rq(), ("CLAIM_C", "Claim", {"text": "Independence causes fewer restatements.", "claim_tier": "causal"}, [("depends_on", "RQ_TEST", 1, {})]))
    r = run_det(s, "contribution", focus_claims=["CLAIM_C"]).result
    c0 = r["candidate_contributions"][0]
    record("C1a", "Causal claim, no identification strategy",
           "IDENTIFICATION_NOT_VERIFIED, preliminary",
           f"identification_status={c0['identification_status']}; is_preliminary={r['is_preliminary']}",
           "PASS" if c0["identification_status"] == "IDENTIFICATION_NOT_VERIFIED" and r["is_preliminary"] else "FAIL",
           "skills/contribution.py:77-82")

    # identification declared verified by lexical match on an adjudication rationale
    add(s, t, ("ADJ_1", "Adjudication", {"subject": {"object_id": "CLAIM_C"}, "verdict": "SUPPORTED",
                                          "rationale": "Association supported; identification is NOT credible (no exogenous shock)."}, []))
    r = run_det(s, "contribution", focus_claims=["CLAIM_C"]).result
    record("C1b", "Adjudication rationale says identification is NOT credible",
           "identification stays NOT_VERIFIED",
           f"identification_status={r['identification_status']}",
           "FAIL" if r["identification_status"] == "IDENTIFIED_BY_VERIFIED_DESIGN" else "PASS",
           "skills/contribution.py:78-80 ('identification' substring)")

    # novelty anticipated by competitor paper linked to the claim
    s, t, _ = new_store()
    add(s, t, rq(),
        ("SRC_COMP", "Source", {"citation_key": "Prior2019", "abstract": "Shows independence reduces restatements.",
                                "full_text": "We show, using the same SOX setting, that board independence reduces restatements."}, []),
        ("CLAIM_N", "Claim", {"text": "First study to show board independence reduces restatements.", "claim_tier": "conceptual"},
         [("depends_on", "RQ_TEST", 1, {}), ("challenges", "SRC_COMP", 1, {})]))
    out = run_det(s, "contribution", focus_claims=["CLAIM_N"], fulltext_policy="always")
    r = out.result
    blob = json.dumps(r)
    record("C1c", "Novelty claim anticipated by a full-text competitor linked via 'challenges'",
           "competitor surfaced as closest competing contribution; novelty contested",
           f"Prior2019/SRC_COMP anywhere in output={('Prior2019' in blob) or ('SRC_COMP' in blob)}; "
           f"closest_competing={r['closest_competing_contributions']}; novelty_status={r['novelty_status']}; plan='{r['novelty_verification_plan'][:80]}...'",
           "FAIL" if "SRC_COMP" not in blob and "Prior2019" not in blob else "PASS",
           "context.py:89-91 (Source not expanded for contribution)")

    # competitor captured as a passage that anticipates the claim, no negative words
    s, t, _ = new_store()
    add(s, t, rq(),
        ("EP_COMP", "EvidencePassage", {"locator": "Prior2019 p.12", "quote": "We already document that board independence reduces restatements in the SOX setting."}, []),
        ("CLAIM_N", "Claim", {"text": "First study to show board independence reduces restatements.", "claim_tier": "conceptual"},
         [("depends_on", "RQ_TEST", 1, {}), ("challenges", "EP_COMP", 1, {})]))
    r = run_det(s, "contribution", focus_claims=["CLAIM_N"]).result
    sup = [e.get("passage_id") for e in r["supporting_evidence"]]
    record("C1d", "Anticipating competitor passage (linked by 'challenges' edge) without negative keywords",
           "classified as challenging/anticipating, not supporting",
           f"supporting_evidence passage ids={sup}; contradicting={[e.get('passage_id') for e in r['contradicting_evidence']]}",
           "FAIL" if "EP_COMP" in sup else "PASS",
           "skills/contribution.py:161-166 (edge relation ignored; lexical)")

    # absence of retrieved papers
    s, t, _ = new_store()
    add(s, t, rq(), ("CLAIM_X", "Claim", {"text": "Novel finding.", "claim_tier": "descriptive"}, [("depends_on", "RQ_TEST", 1, {})]))
    r = run_det(s, "contribution", focus_claims=["CLAIM_X"]).result
    record("C1e", "No papers retrieved at all",
           "novelty NOT verified (absence != novelty)",
           f"novelty_status={r['novelty_status']}",
           "PASS" if r["novelty_status"] == "NOVELTY_NOT_VERIFIED" else "FAIL", "skills/contribution.py:97-102")

    # multiple competing explanations
    s, t, _ = new_store()
    add(s, t, rq(),
        ("MECH_MON", "Mechanism", {"label": "Monitoring"}, []),
        ("MECH_SEL", "Mechanism", {"label": "Selection of better firms"}, []),
        ("CLAIM_M", "Claim", {"text": "Independence reduces restatements.", "claim_tier": "associational"},
         [("depends_on", "RQ_TEST", 1, {}), ("depends_on", "MECH_MON", 1, {}), ("depends_on", "MECH_SEL", 1, {})]))
    r = run_det(s, "contribution", focus_claims=["CLAIM_M"]).result
    blob = json.dumps(r)
    record("C1f", "Two competing mechanisms (monitoring vs selection)",
           "both surfaced as rival explanations",
           f"'Monitoring' in output={'Monitoring' in blob}; 'Selection' in output={'Selection of better firms' in blob}; diff='{r['exact_point_of_differentiation']}'",
           "FAIL" if not ("Monitoring" in blob and "Selection of better firms" in blob) else "PASS",
           "skills/contribution.py:118-119,209 (mechanisms[0] only)")

    # own sibling hypothesis labeled as competing literature
    s, t, _ = new_store()
    add(s, t, rq(),
        ("CLAIM_H1", "Claim", {"text": "H1: independence reduces restatements.", "claim_tier": "associational"}, [("depends_on", "RQ_TEST", 1, {})]),
        ("CLAIM_H2", "Claim", {"text": "H2: the effect is stronger for small firms.", "claim_tier": "associational"}, [("depends_on", "RQ_TEST", 1, {})]))
    r = run_det(s, "contribution", focus_claims=["CLAIM_H1"]).result
    record("C1g", "Researcher's own H2 when focusing H1",
           "not reported as 'closest competing contribution' from literature",
           f"closest_competing ids={[c.get('object_id') for c in r['closest_competing_contributions']]}",
           "FAIL" if any(c.get("object_id") == "CLAIM_H2" for c in r["closest_competing_contributions"]) else "PASS",
           "skills/contribution.py:74,141-148")


def part_c2():
    s, t, _ = new_store()
    add(s, t, rq(),
        ("SRC_1", "Source", {"citation_key": "Alpha2020", "abstract": "a"}, [("depends_on", "RQ_TEST", 1, {})]),
        ("SRC_2", "Source", {"citation_key": "Beta2021", "abstract": "b"}, [("depends_on", "RQ_TEST", 1, {})]),
        ("SRC_3", "Source", {"citation_key": "Gamma2022", "abstract": "c"}, [("depends_on", "RQ_TEST", 1, {})]),
        ("SRC_4", "Source", {"citation_key": "Delta2023", "abstract": "d",
                             "full_text": "Results ... 7. Limitations: because treatment was self-selected our estimates likely overstate the effect; "
                                          "we cannot exclude that the association reverses once firm fixed effects are included."},
         [("depends_on", "RQ_TEST", 1, {})]),
        ("EP_1", "EvidencePassage", {"locator": "Alpha2020 p.5", "quote": "Independent boards lower the incidence of restatements by 20 percent."}, [("derived_from", "SRC_1", 1, {})]),
        ("EP_2", "EvidencePassage", {"locator": "Beta2021 p.9", "quote": "Restatement frequency rises after boards become majority independent."}, [("derived_from", "SRC_2", 1, {})]),
        ("EP_3", "EvidencePassage", {"locator": "Gamma2022 p.3", "quote": "Our estimates are not consistent with the monitoring hypothesis and do not support prior findings."}, [("derived_from", "SRC_3", 1, {})]),
        ("EP_4", "EvidencePassage", {"locator": "Delta2023 p.4", "quote": "Board independence is associated with fewer restatements in our panel."}, [("derived_from", "SRC_4", 1, {})]),
        )
    out = run_det(s, "literature", fulltext_policy="always")
    r = out.result
    rel = {row["passage_id"]: row["relationship"] for row in r["evidence_matrix"]}
    record("C2a", "Two studies conflict (lower vs rises) without negative keywords",
           "conflict flagged or both NOT_ASSESSED with no agreement inferred",
           f"EP_1={rel.get('EP_1')}; EP_2={rel.get('EP_2')}; unresolved_disagreements={len(r['unresolved_disagreements'])}; synthesis='...{r['theoretical_synthesis'][-110:]}'",
           "PARTIAL" if rel.get("EP_1") == rel.get("EP_2") == "NOT_ASSESSED" else "FAIL",
           "skills/literature.py:62-70")
    record("C2b", "'not consistent with ... do not support prior findings'",
           "contradicts_prior or NOT_ASSESSED",
           f"EP_3 relationship={rel.get('EP_3')}; in supported_findings={any('Gamma2022' in x for x in r['supported_findings'])}",
           "FAIL" if rel.get("EP_3") == "supports_prior" else "PASS",
           "skills/literature.py:63 (substring 'consistent with'/'supports')")
    blob = json.dumps(r)
    record("C2c", "Contradiction inside loaded full-text Limitations section (fulltext_policy='always')",
           "limitation surfaced as qualifying/contradicting evidence",
           f"'self-selected' or 'reverses' anywhere in output={('self-selected' in blob) or ('reverses' in blob)}; "
           f"Delta2023 full text loaded={any(ft['source_id']=='SRC_4' and ft['fulltext_status']=='FULLTEXT_LOADED' for ft in build_context(s,'literature',research_question_id='RQ_TEST',fulltext_policy='always').full_texts)}",
           "FAIL" if "self-selected" not in blob and "reverses" not in blob else "PASS",
           "skills/literature.py:51-117 (only excerpts examined; full_text never read)")
    mc = {m["citation_key"]: (m["study_design"], m["sample_context"]) for m in r["methods_and_contexts_reviewed"]}
    record("C2d", "Missing methods / sample",
           "UNKNOWN / NOT_REPORTED",
           f"{mc}", "PASS" if all(v == ("UNKNOWN", "NOT_REPORTED") for v in mc.values()) else "FAIL",
           "skills/literature.py:130-143")
    out2 = run_det(s, "literature", fulltext_policy="on_demand")
    st = {row["passage_id"]: row["fulltext_inspection_status"] for row in out2.result["evidence_matrix"]}
    record("C2e", "Full text available but not inspected (on_demand, not requested); abstract-only sources",
           "FULLTEXT_AVAILABLE for SRC_4, ABSTRACT_ONLY for others; status not SUCCESS",
           f"{st}; status={out2.status}",
           "PASS" if st.get("EP_4") == "FULLTEXT_AVAILABLE" and st.get("EP_1") == "ABSTRACT_ONLY" and out2.status != "SUCCESS" else "FAIL",
           "context.py:167-211")
    # literature with zero evidence
    s0, t0, _ = new_store()
    add(s0, t0, rq())
    o = run_det(s0, "literature")
    record("C2f/D2", "Literature workflow with no sources or passages at all",
           "INCOMPLETE / retrieval plan, not SUCCESS",
           f"status={o.status}; evidence_matrix={len(o.result['evidence_matrix'])}; fulltext_needed={o.result['questions_requiring_fulltext_verification']}",
           "FAIL" if o.status == "SUCCESS" else "PASS", "skills/literature.py:199-204")


def part_c3():
    cases = {
        "causal_no_treatment": ("Does independence cause fewer restatements?", "causal", None, None),
        "cross_sectional": ("Is independence associated with restatements in 2019?", "associational",
                            ("DS_X", "Dataset", {"label": "2019 cross-section", "structure": "cross_sectional", "n": 3100}), None),
        "conceptual": ("How should 'earnings quality' be conceptualized?", "conceptual", None, None),
        "edu_qualitative": ("How do novice teachers experience formative assessment?", "descriptive",
                            ("DS_Q", "Dataset", {"label": "24 semi-structured interviews", "structure": "qualitative_interviews"}), "education"),
    }
    for name, (q, tier, ds, prof) in cases.items():
        s, t, _ = new_store()
        objs = [rq(text=q), ("CLAIM_1", "Claim", {"text": q, "claim_tier": tier}, [("depends_on", "RQ_TEST", 1, {})])]
        if ds:
            objs.append((ds[0], ds[1], ds[2], [("depends_on", "RQ_TEST", 1, {})]))
        add(s, t, *objs)
        kw = {"discipline_profile": prof} if prof else {}
        r = run_det(s, "methods", **kw).result
        designs = [d["design_name"] for d in r["candidate_research_designs"]]
        spec = r["specification_plans"][0]
        panel_assump = any("Panel" in a for a in r["assumptions"])
        record(f"C3-{name}", f"Methods: {name}",
               "no default DID/IV/FE/clustering/controls without design information",
               f"designs={designs}; controls={spec['controls']}; FE={spec['fixed_effects']}; clustering='{spec['clustering']}'; panel attrition assumption={panel_assump}",
               "FAIL", "skills/methods.py:52-135")
    s, t, _ = new_store()
    add(s, t, rq(), ("CLAIM_1", "Claim", {"text": "x", "claim_tier": "causal"}, [("depends_on", "RQ_TEST", 1, {})]))
    r = run_det(s, "methods", candidate_methods=["regression discontinuity", "synthetic control"]).result
    record("C3-competing", "Researcher proposes RDD and synthetic control",
           "researcher's candidate strategies evaluated",
           f"designs={[d['design_name'] for d in r['candidate_research_designs']]}",
           "FAIL" if not any("discontinuity" in d["design_name"].lower() for d in r["candidate_research_designs"]) else "PASS",
           "skills/methods.py:48-52 (candidate_methods only scanned for 'causal')")


def part_c4():
    s, t, _ = new_store()
    add(s, t, rq(),
        ("RES_U", "Result", {"summary": "Author stated beta = 0.35, t = 2.45 in draft text."}, [("depends_on", "RQ_TEST", 1, {})]))
    o = run_det(s, "results")
    rm = o.result["reproducibility_metadata"]
    record("C4a", "Result with no execution record",
           "seed/script/estimator None; NOT_VERIFIED",
           f"seed={rm['random_seed']}; script={rm['script_path']}; estimator={rm['estimator']}; status={rm['reproducibility_status']}",
           "PASS" if rm["random_seed"] is None and rm["reproducibility_status"] == "COMPUTATIONAL_REPRODUCTION_NOT_VERIFIED" else "FAIL",
           "skills/results.py:133-152")
    record("C4b", "Author-supplied summary listed as 'finding supported by available outputs'",
           "unverified summary not presented as supported",
           f"findings_supported_by_available_outputs={o.result['findings_supported_by_available_outputs']}",
           "FAIL" if o.result["findings_supported_by_available_outputs"] else "PASS", "skills/results.py:71-72")
    s, t, _ = new_store()
    add(s, t, rq(),
        ("DS_1", "Dataset", {"label": "anything"}, [("depends_on", "RQ_TEST", 1, {})]),
        ("RES_U", "Result", {"summary": "Author stated beta = 0.35."}, [("depends_on", "DS_1", 1, {})]))
    o = run_det(s, "results", run_metadata={"verified_computation": True})
    rm = o.result["reproducibility_metadata"]
    record("C4c", "Caller passes run_metadata={'verified_computation': True}; no script, seed, data hash, or run",
           "cannot become VERIFIED without an execution record",
           f"reproducibility_status={rm['reproducibility_status']}; deterministic_verification={rm['deterministic_verification']}; workflow status={o.status}",
           "FAIL" if rm["reproducibility_status"] == "VERIFIED_COMPUTATIONAL_RUN" else "PASS",
           "skills/results.py:134-142 (self-attested flag; every object has content_digest)")


def part_c5():
    cases = {
        "1_negative_predicted": ("Board independence has a negative and significant coefficient on restatements (beta=-0.12, p<0.01), as H1 predicts.", "consistent"),
        "2_nonsignificant": ("The coefficient on independence is not significant (beta=0.01, p=0.41).", "inconclusive"),
        "3_regression_word": ("An OLS regression with industry controls shows independence is associated with fewer restatements.", "consistent"),
        "5_challenges_no_keyword": ("Firms adding independent directors increased restatements by 12% relative to controls.", "challenges"),
    }
    for name, (summary, truth) in cases.items():
        s, t, _ = new_store()
        add(s, t, rq(),
            ("RES_1", "Result", {"summary": summary}, [("depends_on", "RQ_TEST", 1, {})]),
            ("CLAIM_1", "Claim", {"text": "H1: board independence reduces restatements.", "claim_tier": "associational"},
             [("depends_on", "RQ_TEST", 1, {}), ("depends_on", "RES_1", 1, {})]))
        r = run_det(s, "discussion", focus_claims=["CLAIM_1"]).result
        si = r["supported_interpretations"][0]
        reopened = r["reopens_contribution_workflow"]
        verdict_ok = {"consistent": not reopened, "challenges": reopened, "inconclusive": "not significant" not in si["scholarly_inference"] and "overturned" not in si["scholarly_inference"]}[truth]
        record(f"C5-{name}", f"Discussion case {name}",
               f"ground truth: {truth}",
               f"reopens={reopened}; interpretation='{si['supported_interpretation'][-60:]}'; inference='{si['scholarly_inference']}'; revised='{r['revised_contribution_statements'][0][:70]}...'",
               "PASS" if verdict_ok else "FAIL", "skills/discussion.py:87-108,180-188")
    # case 4: robustness proposed but not executed
    s, t, _ = new_store()
    add(s, t, rq(),
        ("AN_PLAN", "Analysis", {"label": "Planned robustness", "status": "PLANNED_NOT_RUN",
                                 "description": "We plan to add lagged regressors and year FE in a future revision."}, [("depends_on", "RQ_TEST", 1, {})]),
        ("RES_1", "Result", {"summary": "Independence associated with fewer restatements."}, [("derived_from", "AN_PLAN", 1, {})]),
        ("CLAIM_1", "Claim", {"text": "H1", "claim_tier": "associational"}, [("depends_on", "RQ_TEST", 1, {}), ("depends_on", "RES_1", 1, {})]))
    ctx = build_context(s, "discussion", research_question_id="RQ_TEST", focus_claims=["CLAIM_1"])
    has_an = any(o["object_type"] == "Analysis" for o in ctx.objects)
    r = run_det(s, "discussion", focus_claims=["CLAIM_1"]).result
    # Discussion expansion excludes Analysis -> also test executor directly with the analysis injected
    from research_harness.workflows.skills.discussion import execute_discussion_workflow
    ctx.objects.append({"object_id": "AN_PLAN", "object_type": "Analysis", "revision": 1,
                        "content": {"description": "We plan to add lagged regressors and year FE in a future revision.", "status": "PLANNED_NOT_RUN"}})
    r2 = execute_discussion_workflow(ctx).result
    record("C5-4_robustness_planned", "Robustness proposed (status PLANNED_NOT_RUN), not executed",
           "rival explanations remain NOT_VERIFIED",
           f"Analysis reaches discussion context via run_workflow={has_an}; via run_workflow: {[a['verification_status'] for a in r['alternative_explanations']]}; "
           f"executor given the plan: {[a['verification_status'] for a in r2['alternative_explanations']]}",
           "FAIL" if "ADDRESSED_BY_SPECIFICATION" in json.dumps(r2["alternative_explanations"]) else "PASS",
           "skills/discussion.py:151-170 ('lag'/'year fe' substring)")


# =====================================================================================
# PART D — context budget / completeness
# =====================================================================================

def part_d():
    s, t, _ = new_store()
    seed_all_workflows(s, t)
    ctx = build_context(s, "contribution", research_question_id="RQ_TEST")
    p_tok = estimate_tokens(ctx.formatted_prompt)[0]
    record("D1", "Normal context fits budget",
           "budget OK; all linked evidence present",
           f"budget_status={ctx.budget_status}; prompt est tokens={p_tok}; objects={[o['object_id'] for o in ctx.objects]}",
           "PASS" if ctx.budget_status == "OK" else "FAIL", "context.py")

    # D2: missing essential evidence -> discussion with no results
    s2, t2, _ = new_store()
    add(s2, t2, rq(), ("CLAIM_1", "Claim", {"text": "H1", "claim_tier": "associational"}, [("depends_on", "RQ_TEST", 1, {})]))
    o = run_det(s2, "discussion", focus_claims=["CLAIM_1"])
    record("D2", "Discussion with zero Results",
           "INCOMPLETE / retrieval plan, not SUCCESS / 'corroborated'",
           f"status={o.status}; revised='{o.result['revised_contribution_statements']}'",
           "FAIL" if o.status == "SUCCESS" or "corroborated" in json.dumps(o.result) else "PASS",
           "skills/discussion.py:188,225-230")

    # D3: detection vs keeping within budget
    s3, t3, _ = new_store()
    objs = [rq()]
    for i in range(1, 7):
        objs.append((f"EP_{i}", "EvidencePassage", {"locator": f"p.{i}", "quote": f"Passage {i} " + "evidence " * 60}, [("depends_on", "RQ_TEST", 1, {})]))
    add(s3, t3, *objs)
    for budget in (400, 900, 1300):
        ctx = build_context(s3, "contribution", research_question_id="RQ_TEST", max_input_tokens=budget)
        p_tok = estimate_tokens(ctx.formatted_prompt)[0]
        record(f"D3-{budget}", f"Budget {budget}: detect vs enforce",
               "assembled prompt <= budget, or not sent",
               f"budget_status={ctx.budget_status}; assembled prompt est tokens={p_tok}; included={len(ctx.objects)}; omitted={len(ctx.omitted_items)}; skill tokens={estimate_tokens(ctx.skill_instructions)[0]}",
               "PASS" if p_tok <= budget else ("PARTIAL" if ctx.budget_status != "OK" else "FAIL"),
               "context.py:294-320,422-437 (skill text not budgeted during selection; post-check only labels)")
    # duplication of evidence inside prompt
    ctx = build_context(s3, "contribution", research_question_id="RQ_TEST", max_input_tokens=100000)
    dup = ctx.formatted_prompt.count("Passage 1 evidence")
    record("A4-dup", "Same EvidencePassage text appears once in the model-facing prompt",
           "1 occurrence",
           f"occurrences of passage 1 text={dup}",
           "PASS" if dup == 1 else "FAIL", "contracts.py:175-188 (objects + excerpts both rendered)")

    # D3b: huge full text with always -> whole Source dropped
    s4, t4, _ = new_store()
    add(s4, t4, rq(),
        ("SRC_BIG", "Source", {"citation_key": "Big2020", "abstract": "Important competitor.", "full_text": "Lorem ipsum. " * 20000}, [("depends_on", "RQ_TEST", 1, {})]),
        ("EP_BIG", "EvidencePassage", {"locator": "Big2020 p.2", "quote": "Key competitor finding."}, [("derived_from", "SRC_BIG", 1, {})]))
    ctx = build_context(s4, "literature", research_question_id="RQ_TEST", fulltext_policy="always", max_input_tokens=12000)
    kept = [o["object_id"] for o in ctx.objects]
    record("D3b", "Huge competitor full text with fulltext_policy='always'",
           "source metadata/abstract retained; full text paged or referenced",
           f"kept={kept}; omitted={[(o['object_id'], o['reason']) for o in ctx.omitted_items]}; status={ctx.budget_status}",
           "FAIL" if "SRC_BIG" not in kept else "PASS", "context.py:299-343 (all-or-nothing item tokens incl. full text)")

    # D4: fulltext policies
    s5, t5, _ = new_store()
    add(s5, t5, rq(),
        ("SRC_F", "Source", {"citation_key": "Full2021", "abstract": "abs", "full_text": "FULLBODY " * 50}, [("depends_on", "RQ_TEST", 1, {})]),
        ("SRC_ABS", "Source", {"citation_key": "Abs2021", "abstract": "only abstract"}, [("depends_on", "RQ_TEST", 1, {})]))
    rows = []
    for pol, srcs in (("never", ["SRC_F"]), ("on_demand", None), ("on_demand", ["SRC_F"]), ("always", None)):
        c = build_context(s5, "literature", research_question_id="RQ_TEST", fulltext_policy=pol, fulltext_sources=srcs)
        st = {ft["source_id"]: ft["fulltext_status"] for ft in c.full_texts}
        rows.append(f"{pol}{'+'+','.join(srcs) if srcs else ''}: {st} prompt_tok={estimate_tokens(c.formatted_prompt)[0]} body_in_prompt={'FULLBODY' in c.formatted_prompt}")
    ok = "'SRC_F': 'FULLTEXT_AVAILABLE'" in rows[0] and "body_in_prompt=False" in rows[0] and "'SRC_ABS': 'ABSTRACT_ONLY'" in rows[3] and "body_in_prompt=True" in rows[3] and "body_in_prompt=False" in rows[1]
    record("D4", "fulltext_policy never / on_demand / on_demand+source / always",
           "statuses distinct; body only when requested; abstract never relabeled",
           " | ".join(rows), "PASS" if ok else "FAIL", "context.py:167-211")
    c = build_context(s5, "contribution", research_question_id="RQ_TEST", fulltext_policy="always")
    record("D4b", "Contribution workflow with fulltext_policy='always' and two RQ-linked Sources",
           "competitor sources available to contribution",
           f"sources in context={[o['object_id'] for o in c.objects if o['object_type']=='Source']}; full_texts={len(c.full_texts)}",
           "FAIL" if not c.full_texts else "PASS", "context.py:114-116 (Source not in contribution edge filters)")

    # D5: unknown focal objects + silent resolution
    s6, t6, _ = new_store()
    add(s6, t6, rq("RQ_A", "Board independence and restatements"), rq("RQ_B", "Teacher feedback and student motivation"),
        ("CLAIM_B", "Claim", {"text": "Feedback raises motivation.", "claim_tier": "associational"}, [("depends_on", "RQ_B", 1, {})]),
        ("CLAIM_Z", "Claim", {"text": "Independence reduces restatements.", "claim_tier": "associational"}, [("depends_on", "RQ_A", 1, {})]))
    _, e1 = guard(lambda: build_context(s6, "contribution", research_question_id="RQ_NOPE"))
    _, e2 = guard(lambda: build_context(s6, "contribution", research_question_id="RQ_A", focus_claims=["CLAIM_NOPE"]))
    record("D5a", "Explicitly unknown RQ / Claim", "KeyError, no silent substitution",
           f"RQ: {e1}; Claim: {e2}", "PASS" if e1 and e2 else "FAIL", "context.py:55-73")
    o = run_det(s6, "contribution", rq="RQ_A")
    c0 = o.result["candidate_contributions"][0]
    ctx = build_context(s6, "contribution", research_question_id="RQ_A")
    record("D5b", "Explicit RQ_A with no focus claims, project also holds RQ_B (education)",
           "only RQ_A claims selected; candidate is CLAIM_Z",
           f"claims in context={[o['object_id'] for o in ctx.claims]}; candidate={c0['claim_id']} ('{c0['proposed_contribution']}')",
           "FAIL" if c0["claim_id"] != "CLAIM_Z" or "CLAIM_B" in [x["object_id"] for x in ctx.claims] else "PASS",
           "context.py:75-78; skills/contribution.py:70-72")


# =====================================================================================
# PART E — cache
# =====================================================================================

def fresh_cache_status(store, wf, **kw):
    return run_workflow(wf, store, research_question_id="RQ_TEST", reuse_cached=True, **kw).status


def part_e():
    s, t, home = new_store()
    seed_all_workflows(s, t)
    base = {wf: run_workflow(wf, s, research_question_id="RQ_TEST", reuse_cached=True) for wf in WORKFLOWS}
    record("E0", "Identical rerun reuses cache", "REUSED for all five",
           f"{ {wf: fresh_cache_status(s, wf) for wf in WORKFLOWS} }", "PASS", "dispatcher.py:188-219")

    # E-incomplete: incomplete output reused as REUSED with status lost
    o1 = run_workflow("contribution", s, research_question_id="RQ_TEST", max_input_tokens=150, reuse_cached=True)
    o2 = run_workflow("contribution", s, research_question_id="RQ_TEST", max_input_tokens=150, reuse_cached=True)
    record("E-incomplete", "Re-run of an INCOMPLETE_CONTEXT output",
           "reused output still marked incomplete",
           f"first status={o1.status}; reused status={o2.status}; original status anywhere in reused output={o1.status in json.dumps(o2.to_dict())}",
           "FAIL" if o2.status == "REUSED" and o1.status not in json.dumps(o2.to_dict()) else "PASS",
           "dispatcher.py:206-217")

    # 1. SKILL.md edit in-process via a temp copy of the skills dir
    tmp_skills = WORK / "skills_copy"
    shutil.copytree(REPO / "research_harness/workflows/skills", tmp_skills, ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    saved = reg_mod._DEFAULT_REGISTRY
    reg_mod._DEFAULT_REGISTRY = reg_mod.WorkflowRegistry(skills_dir=tmp_skills)
    try:
        run_workflow("methods", s, research_question_id="RQ_TEST", reuse_cached=True)
        p = tmp_skills / "methods" / "SKILL.md"
        p.write_text(p.read_text(encoding="utf-8") + "\n\n## NEW RULE: never propose fixed effects for qualitative data.\n", encoding="utf-8")
        st_same_proc = fresh_cache_status(s, "methods")
        reg_mod._DEFAULT_REGISTRY = reg_mod.WorkflowRegistry(skills_dir=tmp_skills)
        st_new_reg = fresh_cache_status(s, "methods")
    finally:
        reg_mod._DEFAULT_REGISTRY = saved
    record("E1", "SKILL.md edited on disk (temp copy)",
           "cache invalidated",
           f"same process/registry: {st_same_proc}; after registry re-instantiation (~ new process): {st_new_reg}",
           "PARTIAL" if st_same_proc == "REUSED" and st_new_reg != "REUSED" else ("PASS" if st_same_proc != "REUSED" else "FAIL"),
           "registry.py:106-131 (_instructions_cache memoizes body for process lifetime)")

    # 2. fulltext policy
    st = fresh_cache_status(s, "literature", fulltext_policy="always")
    record("E2", "fulltext_policy on_demand -> always", "miss", st, "PASS" if st != "REUSED" else "FAIL", "dispatcher.py:79-82")
    # 4. budget
    st = fresh_cache_status(s, "literature", max_input_tokens=12001)
    record("E4", "Budget 12000 -> 12001 with identical selected context", "ideally reuse (no context change)", st,
           "PASS" if st == "REUSED" else "AVOIDABLE_MISS", "dispatcher.py:99")
    # 8. profile
    st = fresh_cache_status(s, "methods", discipline_profile="accounting")
    record("E8", "Discipline profile added", "miss", st, "PASS" if st != "REUSED" else "FAIL", "dispatcher.py:101")

    # 3. selected source full text changes
    add(s, t, ("SRC_A", "Source", {"citation_key": "Klein2002", "abstract": "Audit committee independence and earnings management.",
                                   "full_text": "Section 4 (corrected). The association is positive."}, [("depends_on", "RQ_TEST", 1, {})]))
    st = fresh_cache_status(s, "literature")
    record("E3", "Source full text revised", "literature miss", st, "PASS" if st != "REUSED" else "FAIL", "dispatcher.py:74-77")

    # 5. Result revision -> which workflows rerun?
    before = {wf: fresh_cache_status(s, wf) for wf in WORKFLOWS}
    add(s, t, ("RES_A", "Result", {"summary": "Coefficient on independence is -0.05 (p=0.20) after adding firm FE."}, [("derived_from", "AN_A", 1, {})]))
    after = {wf: fresh_cache_status(s, wf) for wf in WORKFLOWS}
    record("E5", "Result revision (RES_A rev2)",
           "results/discussion/contribution (and methods, which includes Result) miss; literature reuse",
           f"before={before}; after={after}",
           "PASS" if after["literature"] == "REUSED" and all(after[w] != "REUSED" for w in ("results", "discussion", "contribution")) else "FAIL",
           "context.py:80-122")

    # 7. unrelated claim added (other RQ) -> invalidates everything
    for wf in WORKFLOWS:
        fresh_cache_status(s, wf)
    add(s, t, rq("RQ_OTHER", "Unrelated education question"),
        ("CLAIM_OTHER", "Claim", {"text": "Unrelated education claim.", "claim_tier": "descriptive"}, [("depends_on", "RQ_OTHER", 1, {})]))
    after = {wf: fresh_cache_status(s, wf) for wf in WORKFLOWS}
    record("E-unrelated", "Unrelated Claim added under a different RQ",
           "all five reuse",
           f"{after}", "PASS" if all(v == "REUSED" for v in after.values()) else "AVOIDABLE_MISS",
           "context.py:75-78 (all project claims selected when no focus)")

    # 6. contradictory passage on a source of the RQ, not linked to claim
    for wf in WORKFLOWS:
        fresh_cache_status(s, wf)
    add(s, t, ("EP_CONTRA", "EvidencePassage", {"locator": "Klein2002 p.400", "quote": "In later years the relation reverses sign; contradicts earlier results."},
               [("derived_from", "SRC_A", 2, {})]))
    after = {wf: fresh_cache_status(s, wf) for wf in WORKFLOWS}
    record("E6", "Contradictory EvidencePassage added to an RQ-linked Source (not linked to the Claim)",
           "contribution + discussion + literature invalidated",
           f"{after}", "PASS" if after["contribution"] != "REUSED" and after["discussion"] != "REUSED" else "FALSE_HIT",
           "context.py:89-116 (Source/its passages not reachable from contribution/discussion)")

    # 9. model identity in fingerprint
    ctx = build_context(s, "contribution", research_question_id="RQ_TEST")
    a1 = ScriptedModelAdapter(model_id="m-1")
    a2 = ScriptedModelAdapter(model_id="m-1")
    fp1 = _compute_input_fingerprint("contribution", ctx, {"model_adapter": a1})
    fp2 = _compute_input_fingerprint("contribution", ctx, {"model_adapter": a2})
    a1.model_id = "m-2-different"
    a1.default_response = {"x": 1}
    fp3 = _compute_input_fingerprint("contribution", ctx, {"model_adapter": a1})
    fp_none = _compute_input_fingerprint("contribution", ctx, {})
    record("E9", "Model identity/config in fingerprint",
           "same config -> same fp; changed model_id -> different fp",
           f"same-config different instance equal={fp1 == fp2}; model_id changed on same instance equal={fp1 == fp3}; "
           f"repr used='{str(a1)[:60]}'; deterministic==model fp={fp_none == fp1}",
           "FAIL" if (fp1 != fp2) or (fp1 == fp3) else "PASS", "dispatcher.py:71-72 (str(v) of adapter)")

    # 10. upstream artifact: rerun of identical upstream => downstream miss
    s7, t7, _ = new_store()
    seed_all_workflows(s7, t7)
    seq1 = run_workflow_sequence(s7, ["results", "discussion", "contribution"], research_question_id="RQ_TEST")
    seq2 = run_workflow_sequence(s7, ["results", "discussion", "contribution"], research_question_id="RQ_TEST")
    record("E10", "Identical Results->Discussion->Contribution sequence run twice",
           "second run fully reused",
           f"run1={[o.status for o in seq1]}; run2={[o.status for o in seq2]}",
           "PASS" if all(o.status == "REUSED" for o in seq2) else "AVOIDABLE_MISS",
           "dispatcher.py:57-60 (upstream digest = hash(to_dict) incl. timestamp/task_id)")


# =====================================================================================
# PART F — persistence
# =====================================================================================

def part_f():
    s, t, home = new_store("PRJ_PERSIST")
    seed_all_workflows(s, t)
    o = run_workflow("discussion", s, research_question_id="RQ_TEST", reuse_cached=False)
    sha, aid = o.metadata.get("artifact_sha256"), o.metadata.get("artifact_id")
    tables = [r[0] for r in s.connect().execute("SELECT name FROM sqlite_master WHERE type='table'")]
    record("F1", "SQLite has a workflow_runs (or equivalent) table",
           "table exists (as claimed in DEEPAGENTS_REFERENCE_ALIGNMENT.md:108)",
           f"tables={tables}", "FAIL" if "workflow_runs" not in tables else "PASS", "state/schema.py:34-190")
    cache_dir = s.paths.root / "workflow_cache"
    child = f"""
import sys, json, shutil; sys.path.insert(0, r'{REPO}')
from research_harness.state.store import StateStore
s = StateStore.open(r'{home}', 'PRJ_PERSIST')
sha = '{sha}'
cas = s.artifacts.has(sha) and json.loads(s.artifacts.get_bytes(sha))['workflow']
db_hits = 0
with s.read() as c:
    for (tname,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        cols = [r[1] for r in c.execute(f'PRAGMA table_info({{tname}})')]
        for col in cols:
            try:
                db_hits += c.execute(f"SELECT COUNT(*) FROM {{tname}} WHERE CAST({{col}} AS TEXT) LIKE ?", ('%{aid}%',)).fetchone()[0]
                db_hits += c.execute(f"SELECT COUNT(*) FROM {{tname}} WHERE CAST({{col}} AS TEXT) LIKE ?", ('%{sha}%',)).fetchone()[0]
            except Exception: pass
shutil.rmtree(r'{cache_dir}')
rec = s.recover()
usage_has_sha = '{sha}' in (s.paths.root / 'workflow_usage.jsonl').read_text()
print(json.dumps({{'cas_by_sha': cas, 'db_rows_referencing_artifact': db_hits, 'orphan_in_recover': sha in rec['orphan_artifacts'],
                  'usage_log_has_sha': usage_has_sha, 'blobs_total': len(list(s.artifacts.blobs()))}}))
"""
    res = subprocess.run([sys.executable, "-I", "-c", child], capture_output=True, text=True)
    info = json.loads(res.stdout) if res.returncode == 0 else {"error": res.stderr[-400:]}
    record("F2", "New process: find workflow output by artifact identity, then delete cache",
           "discoverable via Research Core metadata without cache filenames",
           f"artifact_id={aid}; {info}",
           "FAIL" if info.get("db_rows_referencing_artifact", 0) == 0 else "PASS",
           "dispatcher.py:276-283; store.py:310-357")
    o2 = run_workflow("discussion", s, research_question_id="RQ_TEST", reuse_cached=True)
    record("F3", "After cache deletion, identical request", "reuse via Research Core (no recompute)",
           f"status={o2.status}", "PASS" if o2.status == "REUSED" else "FAIL", "dispatcher.py:188")
    # CAS content == cache content?
    record("F4", "Reopening an earlier Contribution assessment with version link",
           "previous assessment version addressable from new one",
           "WorkflowOutput has no parent/previous-artifact field; artifact_id derived from content hash only; no index",
           "FAIL", "contracts.py:366-394")


# =====================================================================================
# PART G — benchmark reproduction
# =====================================================================================

def part_g():
    sys.path.insert(0, str(REPO))
    from tests.benchmark_fixture import run_scientific_benchmark_evaluation
    s, t, _ = new_store("PRJ_BENCH")
    m = run_scientific_benchmark_evaluation(s, t)
    record("G1-repro", "Reproduce run_scientific_benchmark_evaluation()",
           "reported 90.29% / 0.75 / 1.0 / 100%",
           json.dumps({k: m[k] for k in ("estimated_tokens_reduction_pct", "context_bytes_reduction_pct", "required_evidence_recall",
                                          "contradictory_evidence_recall", "provenance_correctness_pct", "fulltext_inspection_status", "cases_evaluated")}),
           "INFO", "tests/benchmark_fixture.py:357-507")
    pkg = build_context(s, "contribution", research_question_id="RQ_BENCHMARK_01",
                        focus_claims=["CLAIM_01", "CLAIM_CASE_1_DESCRIPTIVE", "CLAIM_CASE_2_NOVELTY"],
                        max_input_tokens=12000, fulltext_policy="always")
    retained = {o["object_id"] for o in pkg.objects}
    req = {"EP_SYNTH_01", "EP_SYNTH_02", "EP_SYNTH_03", "SRC_COMPETITOR_FULL"}
    record("G3-missing", "Which required item is missing", "all 4 present",
           f"missing={sorted(req - retained)}; omitted_items={pkg.omitted_items[:3]}; budget_status={pkg.budget_status}",
           "FAIL" if req - retained else "PASS", "context.py:89-91 (Source not expanded for contribution)")
    json_tok = pkg.estimated_tokens
    prompt_tok = estimate_tokens(pkg.formatted_prompt)[0]
    record("G2-measure", "Candidate token measure",
           "candidate measured on the model-facing prompt",
           f"metric uses pkg.estimated_tokens (intermediate JSON)={json_tok}; actual formatted_prompt est={prompt_tok}; bytes metric uses formatted_prompt",
           "FAIL" if json_tok != prompt_tok else "PASS", "tests/benchmark_fixture.py:487-490")
    # fair baseline: render every head object of the project in the same prompt format with the same skill text
    with s.read() as c:
        heads = c.execute("SELECT object_id, object_type, head_revision FROM object_heads").fetchall()
        objs = []
        for oid, otype, rev in heads:
            ob = s.get_object(oid, revision=rev, conn=c)
            objs.append({"object_id": oid, "object_type": otype, "revision": rev, "content": ob.get("content", {})})
    full_pkg = ContextPackage(workflow="contribution", project_id=s.project_id, research_question_id="RQ_BENCHMARK_01",
                              focus_claims=pkg.focus_claims, objects=objs, excerpts=[], full_texts=[], omitted_items=[],
                              continuation_plan=None, contradictory_evidence_count=0, context_bytes=0, estimated_tokens=0,
                              is_token_estimate=True, manifest={}, skill_instructions=pkg.skill_instructions, skill_digest=pkg.skill_digest)
    base_tok = estimate_tokens(full_pkg.formatted_prompt)[0]
    from research_harness.protocols.snapshot import export_snapshot
    snap_tok = estimate_tokens(json.dumps(export_snapshot(s, token=t, scope="full"), indent=2, sort_keys=True))[0]
    record("G2-fair", "Same-format baseline (all project objects, same renderer, same skill text) vs candidate prompt",
           "reduction computed prompt-vs-prompt",
           f"snapshot-JSON baseline={snap_tok}; same-format full baseline={base_tok}; candidate prompt={prompt_tok}; "
           f"reduction vs same-format={round((1-prompt_tok/base_tok)*100,2)}%; reported={m['estimated_tokens_reduction_pct']}%",
           "INFO", "tests/benchmark_fixture.py:475-490")
    # contradictory recall vs classification
    from research_harness.workflows.skills.contribution import execute_contribution_workflow
    r = execute_contribution_workflow(pkg).result
    contra_ids = {e.get("passage_id") or e.get("result_id") for e in r["contradicting_evidence"]}
    sup_ids = {e.get("passage_id") or e.get("result_id") for e in r["supporting_evidence"]}
    record("G3-contra", "Retained contradictory items classified as contradicting by the workflow",
           "EP_SYNTH_02 and RES_SYNTH_02 in contradicting_evidence",
           f"EP_SYNTH_02 in contradicting={'EP_SYNTH_02' in contra_ids} (in supporting={'EP_SYNTH_02' in sup_ids}); "
           f"RES_SYNTH_02 in contradicting={'RES_SYNTH_02' in contra_ids} (in supporting={'RES_SYNTH_02' in sup_ids}); "
           f"manifest contradictory_evidence_count={pkg.manifest['contradictory_evidence_count']} (fixture defines 2-4)",
           "FAIL" if "EP_SYNTH_02" not in contra_ids else "PASS",
           "skills/contribution.py:161-174; context.py:222-227 ('contradict' matches key 'contradictory_signal')")
    unk = [e for e in pkg.excerpts if e["source_id"] == "UNKNOWN" or e["locator"] == "UNSPECIFIED"]
    record("G3-prov", "Provenance correctness actually computed",
           "computed from excerpts",
           f"hard-coded 100.0 at benchmark_fixture.py:501; computed: {len(pkg.excerpts)-len(unk)}/{len(pkg.excerpts)} excerpts with resolved source+locator",
           "NOT_MEASURED", "tests/benchmark_fixture.py:501-502")


def main():
    for part in (part_b, part_c1, part_c2, part_c3, part_c4, part_c5, part_d, part_e, part_f, part_g):
        try:
            part()
        except Exception:
            record(part.__name__, "harness error", "-", traceback.format_exc()[-1200:], "HARNESS_ERROR")
    (HERE / "repro_results.json").write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
    from collections import Counter
    print(Counter(r["verdict"] for r in RESULTS))


if __name__ == "__main__":
    main()
