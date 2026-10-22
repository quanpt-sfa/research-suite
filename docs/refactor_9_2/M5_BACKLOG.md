# M5 backlog (items found during WP3 review, not in the scope of WP4–WP5)

Each item: what is wrong or untested today, where, how to show it, and what a fix has to do. None is fixed by WP4 or WP5.

## B1. Partial provider usage

* **Where:** `research_harness/tasks/budget.py`, `BudgetedAdapter._persist_charge`.
* **Today:** `reported = tin is not None or tout is not None`. If a provider reports only one side (for instance output tokens without input tokens, or the
  reverse), the call is charged as reported: the missing side counts as 0. The call is under-charged, and the budget can be exceeded without any block.
  `usage_source` says `provider_reported` even though half of the figure is missing.
* **How to show:** a fake adapter returning `SimpleNamespace(input_tokens=None, output_tokens=120)`; the charge row has `charged_tokens == 120`, while the
  reservation was several thousand. (Today only the all-missing case is tested: `test_tokens_charged_for_a_provider_that_reports_no_usage_survive_a_restart`.)
* **Fix has to:** charge a missing side at its reservation (`input_reserved` or `output_reserved`), record `usage_source: "partial"` with which side was
  reserved, and add tests for input-only, output-only, zero, negative and non-integer values (a provider returning a float or a string).
* **Also check:** cache-read and reasoning tokens (`TaskUsage.cache_read_tokens`) are not part of the charge today; decide whether reasoning tokens that a
  provider reports separately must be added.

## B2. Fencing at the result-write boundary

* **Where:** `research_harness/tasks/engine.py`, `_run_loop`: `run.still_mine()` is evaluated, then the step row is written by `workflow_runs.append_run`.
* **Today tested:** a lease lost *during* a step (the thief writes while the step runs) is detected by the check before the write
  (`test_a_runner_that_lost_its_lease_does_not_overwrite_the_task_or_register_a_success`), and a step longer than the TTL keeps its lease.
* **Not tested, and a real window:** the lease is lost *between* `still_mine()` and the registry write (another runner takes the task in those
  milliseconds): the step is registered as SUCCEEDED by a runner that no longer holds the task. The write of the step row is not conditional on the
  task revision because the registry is append-only and separate from the object store.
* **Also untested:** the process dying between the capability's own commits and the step row (the capability's objects exist, no step summary: the next
  run redoes the step, relying on the capability's own idempotence); a heartbeat that fails repeatedly with transient database errors (it keeps trying,
  and the final check is what decides); `break_lease` taken while the previous runner is still alive and mid-step.
* **Fix has to:** write the step row and a task-revision check in one transaction (a `task_step` row carrying the task revision it was made under, and an
  insert that fails if the task head moved: possible with a trigger or by writing the step summary as part of a task revision), then test the window with a
  barrier between the check and the write, and kill the process at each of the three points.

## B3. Smaller items noticed

* `task status` shows `produced_by` only for the newest recorded model; when a step was redone by another model, the older rows remain in the registry
  but are not listed. A `task history STEP` view would show them.
* Proposals made by an earlier model for statements the researcher has not confirmed stay on record after a model change and still appear in
  `research adjudicate sheet` next to the new model's proposals; the sheet could mark them superseded.
* Tasks created before the charge rows existed (2026-10-22) report a spend of 0. No such task exists outside tests.

## B4. Noticed in WP5 review follow-up and WP6 (2026-10-22), not fixed

* **Relationship naming is the weak point of contradiction detection.** The landscape compares findings only by the relationship label the extraction gave them (case and
  spacing normalised, plus equivalences the researcher declares with `--same`). A contradiction stated in other words is missed; the coverage counts findings without a label but cannot
  count findings whose label differs. A measured answer needs the researcher's own list of known contradictions in a real pilot corpus (not written here); a model-assisted label
  normaliser would be a proposal to the researcher, never a decision, and should be evaluated against that list before it is adopted.
* **No model-proposed method assessment.** `landscape assess` is written by the researcher only. A model could draft criteria and threats from the paper's design passages and the
  methodological literature in the library; it would be stored as a proposal, and the cap in `method_verdict` would still apply to anything the researcher confirms. Not built.
* **Introduction arguments and contribution claims are not part of the landscape** (`RP_INTRO_ARGUMENT`, `RP_CONTRIBUTION` are not run by `landscape build`): WP7 needs the claimed
  contribution of the closest papers and will add that step with its own tests.
* **Retrieval top-n union.** The reading list is the union of the R1 and R2 lists, each cut at `top_n` per tier and query, so it can hold up to twice that; a paper ranked below the
  cut in both lists is not shown. Whether a larger default `top_n` is worth the reading cost is an empirical question for a real corpus.
* **Landscape entries join the ECA sampling pool.** Every landscape entry is a STANDARD statement sampled at the D15 rate; a large landscape therefore adds adjudication work. The
  coverage report already shows it; whether findings should default to HIGH once a candidate rests on them is decided in WP7 (`use_in_conclusion`).

## B5. WP6.1: what is not validated, and what WP7 must change so that contribution generation fits each research genre (2026-10-22; first written as 2026-10-23 in error)

Not validated (needs the researcher's materials, not more code): relation proposals, implied assumptions and the levels-of-analysis guard have only been run on invented papers and scripted models
(`REAL_CORPUS_VALIDATION_PENDING`). A model can state levels of analysis that are wrong, or use a synonym ("team" for "group") that makes the guard disagree with the researcher; the researcher's `add-proposal` path
does not apply the levels check for that reason. Whether the three execution contracts (`LIB_DW_CRITICAL_SYNTHESIS`, `LIB_DW_CONCEPTUAL_ARGUMENT`, `LIB_DW_QUALITATIVE_INQUIRY`) describe how a researcher in those traditions
actually works is a question for such a researcher; they are `EXTRACTED` skeletons. The education profile (`workflows/profiles/education.py`) is still a quantitative-policy checklist.

For WP7 (not started; blocked on the WP14 acceptance):

1. **A candidate declares the kind of contribution it would make**, taken from the problem's inquiry and the claims it rests on: an empirical gap (a finding not yet reported for a scope), a conceptual one (a reconceptualisation
   of a confirmed tension), an interpretive one, a methodological one. The checks that follow differ by kind (route considerations), and a candidate must say which claim kinds it rests on.
2. **Candidates rest on confirmed relations, or say they do not.** A candidate built on a relation that is `UNCONFIRMED`, `needs_reassessment`, or whose recognition status is `RECOGNITION_NOT_ASSESSED`, `POTENTIAL_ADDRESSING_SOURCES`, `INSPECTION_INCOMPLETE` or `ADDRESSED_IN_INSPECTED_SET` must show that state; a `NOT_FOUND_IN_INSPECTED_SET` is only a statement about the inspected set in its corpus snapshot and cannot become "novel" (revised in the remediation of 2026-10-22; this item first referred to the earlier statuses).
3. **Pre-emption for conceptual candidates.** The WP14 precedence rules and truth table are written for empirical claims (a study that already found the result). For a conceptual candidate "pre-empted" means that a theory
   already makes the argument; the researcher's expert cases must cover those, and the rules must be extended before WP8 (they are the researcher's to write; the suite does not fill them).
4. **`use_in_conclusion` for landscape entries**: whether a theoretical claim or an interpretive finding that a candidate rests on becomes a HIGH ECA statement like a finding (decision for WP7).
5. **The goal `discover_contribution` selects its execution contract from the problem's inquiry** (a spec whose `study_designs` and `research_purposes` match), and a project with no recorded inquiry is told so, not
   defaulted to the regression workflow.
6. **Currency propagates**: `needs_reassessment` on an inference must carry into every candidate that cites it.
7. The ordering gap noticed in the critical-synthesis contract: redoing `identify_tensions` after a loop-back does not make `search_recognition` runnable again (the engine reopens a step only on a diagnostic signal); the
   researcher records the step again by hand. A rule "redo downstream steps when an upstream decide step is redone" would need an engine change and is not part of WP6.1.

## B6. WP6.1 remediation (2026-10-22): what remains open

B1 and B2 above are still open and unchanged. No paid-model batch run was started by the remediation.

* **The three execution contracts are `EXTRACTED` skeletons, now with methods and findings, still not validated by a researcher in those traditions.** The remediation fixed the structural error (no
  methods/results for a critical review, a conceptual paper, a qualitative study) and defined for each step what it is for, what completes it (`completed_by`) and when it applies, but whether the steps and
  their order match how such researchers work is the Research Owner's judgement (`REAL_CORPUS_VALIDATION_PENDING`). Which appraisal tool a critical review uses, if any, is deliberately left to the researcher's protocol.
* **A step completed by "the researcher recorded it" is labelled `RECORDED_BY_RESEARCHER`.** The suite does not check the work behind such a record; a stage with only such steps reports `REQUIRES_VERIFICATION`.
* **Recognition inspection is only as good as the inspected set.** Candidates come from word matching (BM25) over the held corpus and the retrieval results, not from a database search or semantic retrieval;
  papers outside the corpus are invisible to it, and abstract-only sources can be listed but not inspected. `NOT_FOUND_IN_INSPECTED_SET` is a statement about the inspected set in its snapshot, never about the literature.
* **`BASIS_CHECKED` is a structural check** (passage exists at the current revision, excerpt verbatim in it, limit stated; argument steps resolve). It does not judge whether the basis supports the criterion.
  Earlier assessments are not upgraded and are shown `BASIS_NOT_TRACEABLE`.
* **The pilot `ai_communication_critical_review` is set up, not run** (`pilots/ai_communication_critical_review/`, status `REAL_CORPUS_VALIDATION_PENDING`): 26 of 27 candidate seeds matched Crossref records
  (`simon1971`, a book chapter, did not); the corpus is metadata only; the review protocol is a `DRAFT` with screening empty and appraisal undeclared. It needs the researcher's adoption of a protocol, lawful full texts,
  and an approved model run before any landscape, relation or tension result exists. The seed list is the assistant's proposal, not a search result and not a screening decision.
* **Redoing an upstream decide step does not reopen downstream steps automatically** (B5 item 7), still an engine change that is not part of WP6.1.
* **`review-protocol` stores the screening log and PRISMA counts as data; there is no flow-diagram renderer** and no import from reference managers or database exports.
* **Not started, on instruction:** WP7, WP8, WP14.

## B7. WP6.2 (2026-10-23): what remains open

B1 and B2 stay open and unchanged. No paid-model run was made for WP6.2; all extraction in the tests and measurements is scripted or rule-based.

* **No benchmark of a real model's claim extraction exists** (`REAL_CORPUS_VALIDATION_PENDING`). Own-versus-background attribution accuracy, claim recall and the fidelity of restatements are unmeasured. They need
  (a) full texts the project may use, (b) gold by people who did not see system output, with disagreements kept and adjudicated (the blank form is `research claims annotation-form`), (c) a bounded, approved model
  run with a budget guard (B1), and (d) the researcher's own domain: SciArg is computer graphics, SciFact is biomedical abstracts, and neither covers conceptual papers or critical reviews.
* **Public benchmarks not used, and why.** SciArg: the data server did not answer from here and no licence is stated (it would be the one public source of own/background labels; the researcher may be able to
  fetch it and clear the licence). CLAIM-BENCH: no licence file, 89 published PDFs in the repository, annotations as text blobs without offsets, AI/ML papers only, evidence excluded from introductions by its guidelines.
  The authors could be asked for a licence.
* **SciFact measures only a lexical selector** on biomedical abstracts, where the claims were written from the abstracts' own citation contexts and so share vocabulary with them. The figures are an upper-leaning
  indication of what BM25 does for the claim follow-up on other kinds of papers, not a measure of it.
* **The reading-cost comparison uses a rule-based stand-in.** Calls, chunks, re-reading, unread text, caps and cache are real properties of the code; the token counts are estimates by a scripted adapter; coverage,
  fidelity and latency of the three strategies were not measured. Candidate B saves calls and tokens here but cannot fill `theory_invoked`, `assumption_tested`, `phenomenon`, `evidence_needed`, `research_question`,
  `design`, and fills the `RP_METHOD` fields only as unclassified candidates (`claim_views.DERIVABILITY`); whether that loss matters is for the researcher to judge.
* **The cache key of the five older protocols still omits the model.** A reuse now reports `reuse.model_matches` and `cached_model`, so it is visible, not silent. Whether those protocols should become model-scoped
  (which re-keys their passages and caches) is a decision for the owner.
* **`RP_LANDSCAPE` still mixes the authors' findings with their reports of earlier work** and caps each field (8/8/8/4/4/5/3/4/3); a capped field is now reported (`cap_saturated`) but the Landscape coverage
  does not surface it yet. A landscape built from an inventory (`claim_views.derive`) would separate attribution and need no cap; not built, because it changes WP6 behaviour and is a WP7 decision.
* **Recognition of already-addressed tensions (task 10) stays in the Landscape's inspection records.** Claim relations carry no recognition status; `CONTRADICTS_WITHIN_SCOPE` and `SAME_IDEA_PROPOSED` say nothing about novelty.
* **`STATED_IN_QUOTE` is a word-overlap check.** A paraphrase reads as inferred (the safe error); a combination of words the text does not state as one fact can read as stated (the unsafe one).
* **Absence-based alignments (`WITHDRAWN`, `NEW_IN_LATER_VERSION`)** carry the other version's coverage caveat and need `missing_information`, but cannot tell a withdrawn claim from one the second extraction missed.
* **No path publishes an inventory into the knowledge library** (ids, pins). The edges a closure would walk exist.
* **GROBID: no adapter, no comparison with `pypdf`.** Docker is not available here. The test to run: structure (sections, references, tables, offsets) on PDFs with known structure from the researcher's fields.
* **ECA sampling workload grows** with every claim of every inventory (as with Landscape entries, B4); ECA still adjudicates a claim against its wording only (`basis_passage` takes the first passage), not against each evidence link.
* **Not started, on instruction:** WP7, WP8, WP14.
