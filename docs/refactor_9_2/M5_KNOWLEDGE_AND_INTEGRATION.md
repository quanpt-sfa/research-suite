# M5 design: knowledge accumulation, domain workflows, integration with Methods and Results

Status: **proposal**. Nothing here is implemented. Part of `PLAN_M5_TASKS_AND_DISCOVERY.md`.

## 6. Knowledge accumulation

### 6.1 Five things that must not be confused

| What | Where it lives | Can be reused as | Can it become knowledge? |
|---|---|---|---|
| **Knowledge** (a method element, a verification rule, a validity assessment) | library `KnowledgeItem` / `MethodPlaybook` | a recommendation within its promoted scope (`lifecycle.match`) | already is, within scope |
| **Hypothesis** (a contribution candidate, a claim not yet tested) | project `ContributionCandidate`, `Claim` | a starting point, never a premise | only through results and the claim loop, then promotion |
| **Approved decision** (keep/drop a candidate, choose a framing) | project `ResearchDecision` | a record of *why* this project went this way | an idea-history item may *cite* it; the decision itself is not knowledge |
| **Method experience** (a step that failed, a cap that had to be raised, a tolerance that was too tight) | `failure_record`, `review_lesson` (existing kinds) | a warning shown with matches | yes, as a lesson with scope |
| **Reviewer comment** (editor says "not novel enough") | new kind `review_outcome` | a statement of a person, with venue and date | never as evidence; it can raise a revisit condition |

**Publication or repeated use is never validation.** `record_usage` already records use without lifting validity (`lifecycle.py` docstring and
`tests/test_library_lifecycle.py`); the same rule is extended to idea-history kinds: a candidate pursued by three projects is three usage records, not a
validated idea. A published paper by the researcher is a `Source` like any other.

### 6.2 New knowledge kinds (library)

Added to `taxonomy/knowledge.json::knowledge_kind` with formalize rules in `lifecycle._KIND_RULES`:

| Kind | Formalize rule (required body fields) | Status values (`body.status`) |
|---|---|---|
| `contribution_idea` | `body.claim`, `body.mechanism`, `body.type`, `body.challenge_status`, `body.coverage` | `PROPOSED`, `PURSUED`, `SET_ASIDE`, `PREEMPTED_WITHIN_SCOPE` |
| `preempted_idea` | `body.candidate_pin`, `body.preempting_passages`, `body.preemption_scope` (which of the 10 dimensions) | `PREEMPTED_WITHIN_SCOPE` |
| `open_disagreement` | `body.positions[]` each with passages, `body.what_would_decide` | `OPEN`, `RESOLVED_IN_SCOPE` |
| `unexplained_result` | `body.result_pin`, `body.run_id`, `body.why_unexplained` | `OPEN`, `EXPLAINED_IN_SCOPE` |
| `review_outcome` | `body.venue`, `body.date`, `body.judgement`, `body.reason` | `RECORDED` |

Every item (new and existing kinds) carries these fields; those that are new for the existing schema are marked *new*:

| Field | Meaning |
|---|---|
| `promotion.scope`, `promotion.exclusions` | existing (I14) |
| provenance: `generated_by`, edges `derived_from` to project objects by pin, `source_project` (*new*: project id + state digest at retrospective) | where it came from |
| `epistemic_status` (*new*): `knowledge`, `hypothesis`, `decision_record`, `experience`, `person_statement` | which of the five rows of 6.1 |
| `validation` | existing three dimensions; for idea kinds `methodological_validity` stays `NOT_ASSESSED` by rule |
| `criticisms` | existing `known_criticisms` pattern |
| `revisit_conditions` (*new*) | structured, see 6.4 |
| `parent` / `superseded_by` | existing lifecycle `SUPERSEDED` + `revise` |
| `reuse_conditions` (*new*) | what a later project must declare for the item to be shown (e.g. same population) |

### 6.3 Lifecycle

`Acquire → Formalize → Verify/Assess → Promote → Reuse → Revise/Supersede`, all existing functions in `knowledge/lifecycle.py`:

* **Acquire**: `retrospective(project)` proposes items at the end of a task (candidates with decisions, pre-emptions, disagreements found in the landscape,
  unexplained results); the researcher acquires them (`acquire(..., acquired_from="retrospective")`). Nothing is acquired automatically.
* **Formalize**: per-kind rule above.
* **Verify/Assess**: `source_fidelity` (passages still verify, adjudications confirmed). For a `preempted_idea`, MEANING_VERIFIED requires the confirmed
  adjudications that made the candidate CHALLENGED.
* **Promote**: explicit, with scope; `promotion_blockers` adds for idea kinds: coverage block present; pre-emption scope present.
* **Reuse**: at the start of a discovery task, `lifecycle.match(problem.scope)` lists prior attempts *before* generation, with their status, scope and
  revisit conditions, and whether those conditions are now met.
* **Revise/Supersede**: `revise` with restriction or extension; a re-opened idea is a new revision, the old one stays.

### 6.4 Revisit only when conditions truly change

`revisit_conditions` are structured, evaluated deterministically against the new project's declared facts (`domain_engine.ProjectFacts`):

```jsonc
[{"type": "data_available", "dataset_kind": "post-2015 AAER labels"},
 {"type": "population_differs", "dimension": "population"},
 {"type": "method_available", "playbook_kind": "staggered DiD"},
 {"type": "results_change", "result": "LIB_RES_…@2"},
 {"type": "corpus_grows", "min_new_full_text_competitors": 3}]
```

An item is shown as *reopenable* only if at least one condition is `SATISFIED` by declared facts; `UNKNOWN` conditions are shown with the fact needed.
Free-text conditions are allowed but are never evaluated as satisfied (shown as "check yourself").

### 6.5 Cross-domain mis-transfer (tests)

* `test_preemption_does_not_transfer_across_population`: an idea pre-empted for US firms is shown for an EU-firm problem as "pre-empted for US firms;
  population differs", never as pre-empted.
* `test_method_experience_scoped_to_design`: a `failure_record` from an archival study is not shown as applicable to a qualitative education study.
* `test_review_outcome_never_evidence`: a `review_outcome` cannot be cited as a passage, cannot be an adjudication basis, cannot change a challenge status.
* `test_reuse_count_does_not_raise_validity`: ten usage records leave `methodological_validity` unchanged.
* `test_empty_context_matches_nothing`: existing rule (`lifecycle.match`), extended to idea kinds.

## 7. Domain workflows: a generic engine, data contracts per domain

* **Generic engine, unchanged code path.** `domain_engine.py` already evaluates stages, steps, entry conditions and `decide` steps from JSON specs.
  Discovery adds a *step family* to `generic.json` (`frame_problem`, `retrieve`, `landscape`, `generate_candidates`, `adversarial_retrieve`,
  `verify_candidates`, `assess_feasibility`, `decide_candidates`) under the `contribution` stage.
* **Domains override data, not code.** A domain spec (e.g. `acc_fraud_detection.json`) may add entry conditions (e.g. "label provenance recorded
  before feasibility"), extra landscape fields (via reading-protocol data), or required dimensions for verification (e.g. `estimand` mandatory for
  causal designs). It may not remove the two mandatory checkpoints (validated by `DomainWorkflowSpec` loading: `SpecError`).
* **Growth without hard-coding:** new domains are JSON files plus reading-protocol data; test `test_new_domain_spec_needs_no_code_change` loads a
  synthetic `education.qualitative` spec and runs the discovery family on scripted adapters.
* **Extensibility targets:**
  * *Conceptual papers*: no data step; feasibility checks argument structure (are the constructs defined, is the mechanism stated) instead of data;
    `design: conceptual` makes `assess_feasibility` use the conceptual checklist.
  * *Qualitative education*: dimensions `population`, `setting`, `theoretical_lens`, `data_source` dominate; `estimand` is `not_applicable`; playbooks
    for econometrics are refused (existing rule: a playbook that does not apply to the study design is refused, `docs/ANALYSIS_PIPELINE.md`).
  * *Finance*: `period` and `data_source` mandatory dimensions; identification assumptions emphasised.
  * *Mixed methods*: candidate has `strands[]`, each verified on its own dimension set.
* Profiles (`workflows/profiles/{accounting,education,finance}.py`) remain prompt-level hints; they do not gate anything (I18: domain behaviour
  lives in execution contracts).

## 9. Integration with Methods and Results

### 9.1 Dependency graph

```
ResearchProblem ─addresses◄─ ResearchTask ─► RetrievalRun ─► Landscape entries ─rests_on◄─ ContributionCandidate
        │                                                                         │   ▲ challenges/supports (EvidencePassage, Adjudication)
        └─derived_from◄─ ResearchQuestion ◄─(promote)                              │
                                                                      ResearchDecision(go_to_methods)
                                                                                   ▼
                                     MethodPlaybook ◄─uses─ AnalysisPlan ─► execution DAG ─► Result ─► Claim ─► ClaimChange
                                                                                                  └──► ResearchDecision(revisit_when_results_change)
```

Labels such as `addresses` or `rests_on` are `edge_kind` values in the provenance of existing `depends_on` / `derived_from` edges, not new relations
(the `edges.relation` SQL CHECK, `state/schema.py:156`, stays as it is).

### 9.2 Impact propagation (selective invalidation)

> **Amended (M5_DESIGN_AMENDMENT_1.md A4, invariant I20):** the first two rows below are superseded. Every absence-based conclusion is bound to a
> `corpus_snapshot_id`; when the corpus changes it is shown as `STALE_FOR_CORPUS` and the delta is screened against every open candidate regardless
> of retrieval rank (exhaustively up to 30 new sources, by a declared prefilter above that). `CHALLENGED` is not re-opened by corpus growth, only by a
> revision or exclusion of the pre-empting source.

Uses `graph/dependency.py::invalidate_dependents` unchanged; M5 only adds edges with the right `dependent` side.

| Change | Marked NEEDS_REVIEW / STALE | Not touched |
|---|---|---|
| New paper imported | `RetrievalRun` (corpus digest changed) → STALE | candidates, until re-retrieval returns a different competitor set |
| Re-retrieval returns a new competitor for CAND_3 | CAND_3 challenge status → `NOT_ASSESSED` for that competitor; others unchanged | CAND_1, CAND_2 |
| Passage no longer verifies (source revised) | landscape entries and adjudications on it (existing ECA rule: bound to text digest) | entries on other passages |
| Result revised | `ResearchDecision(revisit_when_results_change)` bound to it; Claims (existing); promise check | candidates not bound to that Result |
| Playbook revised | feasibility of candidates that cited it | challenge status |
| Problem reframed (new revision) | task → STALE; RQ `derived_from` edge reported stale (`stale_pinned_edges`) | library items |

Test `test_selective_invalidation_touches_only_dependents` asserts the "not touched" column.

### 9.3 Back-propagation

* **Results → Introduction/Contribution**: when a Result bound to a `revisit_when_results_change` decision is revised, the decision is NEEDS_REVIEW and
  `promise_check` (existing, `knowledge/loop.py`) runs on the `ContributionArgument` seeded from the pursued candidate; outputs are *suggestions*.
* **Results → Discussion**: unexplained results become `unexplained_result` proposals at retrospective.
* **Project → Library**: only through retrospective + explicit promotion (I14). Nothing flows into the library automatically.
* **Library → Project**: closure import pins (`closure.use_in_project`); `check_updates` reports newer revisions; adopting one is a decision.
