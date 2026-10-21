# M5 design: Contribution Discovery protocol

Status: **proposal**. Nothing here is implemented. Part of `PLAN_M5_TASKS_AND_DISCOVERY.md`. Existing-code references point at `main` `c6397de`.

**What the workflow is for.** A researcher starts from a problem and no manuscript. The workflow helps them learn three reliable things: (1) what the
literature they have actually establishes about the problem, separated into what authors claim, what their evidence permits and what the suite infers;
(2) which candidate contributions are already pre-empted *within the searched corpus*, with the passages that show it; (3) what a surviving candidate
would need (data, measures, assumptions) before it can become a study. It does **not** tell the researcher that anything is novel, and it decides nothing.

```
5.1 problem framing ─► 5.3 retrieval (progressive, recorded) ─► 5.2 landscape ─► 5.4 candidate generation (discovery)
    ─► 5.3 candidate-specific adversarial retrieval ─► 5.5 adversarial verification (confirmation) ─► 5.6 feasibility ─► 5.7 researcher decisions
```

Every arrow is a task step (`M5_TASKS_AND_DATA_MODEL.md`), registered in `workflow_runs`, with its inputs pinned. The two `decide` checkpoints that cannot be
removed are after 5.1 (which interpretations to pursue) and 5.7 (what happens to each candidate).

## 5.1 Problem framing

* Input: the researcher's statement as a `ResearchProblem` (kind, scope, exclusions).
* Optional model step `frame_problem`: proposes up to *k* (default 4) interpretations, each with the construct(s) it involves, the implied unit of
  analysis, what kind of answer it would admit (causal, predictive, descriptive, conceptual), and the terms a search would need. Every proposal is
  `generated_by: model:…`, status `PROPOSED`.
* Several interpretations may be kept at once (`chosen_interpretations` is a list). Retrieval runs per chosen interpretation and records which one
  produced each hit.
* Failure modes guarded: the model narrowing the problem to what it knows (the researcher sees all proposals and their differences, and can add their own);
  the model inventing a scope (scope fields are copied from the researcher's statement only; proposed scope changes are shown as changes).

## 5.2 Landscape

Built from the reading list (5.3) with existing capabilities: `RP_THEORY` and `RP_CONTRIBUTION` through `knowledge/literature.py::run_literature`,
`knowledge/argument.py::analyze_introduction` on the closest papers, and a new data-only protocol `RP_LANDSCAPE` (fields: `finding`, `boundary_condition`,
`identifying_assumption`, `measurement`, `design`, `stated_limitation`, `open_question`, each with a quote, caps per field as in RP_METHOD v4).

The landscape is a table of **landscape entries**, each one of:

| Entry type | Content | Basis required |
|---|---|---|
| construct | name, definitions per source, flag if definitions differ | ≥1 verified passage per definition |
| mechanism | stated causal/explanatory path | passage |
| identifying_assumption | assumption a design relies on; `tested_in_source` yes/no/not_stated | passage |
| measurement | proxy/measure for a construct | passage |
| design | study design and data regime | passage |
| finding | result with its population, period, estimand | passage (+ table locator if numeric) |
| contradiction | two findings that disagree, with the conditions under which each holds | two passages; the *contradiction itself* is a suite inference |
| boundary | where a finding is stated not to hold | passage |
| unresolved_question | authors' own stated open question | passage |
| method_limitation | authors' stated or suite-inferred limitation | passage; suite-inferred ones marked |

> **Amended (M5_DESIGN_AMENDMENT_1.md A5):** the `evidence_permits` layer below is withdrawn. Findings carry three separate dimensions — `reading`,
> `inference` (needs a method assessment, not ECA), `reproduction` — and ECA alone never moves a finding beyond `READING_CONFIRMED`.

Each entry carries **three separate layers** (spec 5.2):

* `author_claim`: what the authors say (quote-verified passage; `AUTHOR_ASSERTED`, I11).
* `evidence_permits`: what their reported evidence supports, *only if* an ECA adjudication on that passage has been run; otherwise `NOT_ASSESSED`.
  Never filled by the model alone.
* `suite_inference`: anything the suite derives (a contradiction between two papers, a gap). `generated_by` names the step; status PROVISIONAL at most;
  ECA HIGH if a candidate rests on it.

The landscape is stored as `KnowledgeItem`-free project objects (a `Landscape` CAS artefact with entries pointing to `EvidencePassage` ids) and rendered as
`research_landscape.md` (export contract).

## 5.3 Progressive retrieval

### Stages

| Stage | Method | Deterministic | Output |
|---|---|---|---|
| R1 lexical | BM25 over stored source sections, passages and library items; query = researcher terms + interpretation terms | yes | ranked hits with matched terms |
| R2 semantic expansion | model proposes synonyms, related constructs and alternative names (e.g. "earnings manipulation" for "fraud"); researcher can edit; re-run R1 with the expanded query | the expansion is a recorded model output; the search after it is deterministic | added hits, each tagged with the expansion term that found it |
| R3 citation / metadata | (a) within corpus: references parsed from stored texts link papers already held; (b) optional external metadata (OpenAlex/Crossref, D17) proposes papers **not** held | (a) yes; (b) deterministic given the API response, which is stored | acquisition list (metadata only) |
| R4 candidate-specific adversarial | after 5.4: each candidate's claim, mechanism and setting become a query (R1+R2 with the candidate's own terms); goal is to find the paper that already does it | yes after expansion | per-candidate competitor list |
| R5 human review | researcher adds papers they know, marks hits irrelevant (with reason), requests acquisition | — | recorded in the `RetrievalRun` |

### Alternatives considered

| Option | Recall on synonyms | Reproducibility | Dependencies | Cost | Decision |
|---|---|---|---|---|---|
| BM25 only | poor ("misstatement" vs "manipulation") | exact | stdlib | none | baseline (R1) |
| BM25 + model query expansion | good for named synonyms | expansion stored, search exact | adapter | 1 call per interpretation/candidate | **chosen** (R1+R2) |
| Dense embeddings (local model) | good for paraphrase | depends on model weights; must pin model hash | `sentence-transformers` / torch (heavy, optional) | CPU time | optional extra later, behind a flag, benchmarked against R1+R2 before adoption |
| Embeddings via API | good | provider model may change silently | network, key | per token | rejected for default: sends paper text to a provider (rights, section 12) |
| Model reads the whole corpus | high for small corpora | not reproducible, costly | adapter | very high | rejected |

**Benchmark design (WP-RET):** a fixed synthetic corpus of ~40 short documents with planted synonyms, paraphrases and decoys, and a labelled
relevance set per query; metrics recall@10, precision@10, MRR. Adoption rule for any retrieval change: recall@10 does not drop on the fixed set, and the
change is reported with both numbers. Real-corpus numbers (Gate M) are reported, never tuned on.

### External metadata (D17), boundaries

* **Opt-in per project** (`--metadata-source openalex|crossref`), off by default; never during CI (tests use recorded responses).
* What is sent: query terms only, never paper text, never researcher notes. A polite-pool `mailto` only if the researcher configures one.
* Pagination with a hard cap (default 200 records per query), backoff on 429/5xx, rate limit configurable; the raw response pages are stored in CAS
  with request URL (without secrets), time and record count.
* Deduplication: DOI normalized (lower-case, strip `https://doi.org/`), then title+year fuzzy key; duplicates kept as aliases.
* **A metadata-only record is never evidence.** It becomes a `Source` with `fulltext_status: MISSING` (existing enum, `protocols/schema.py`), can be listed
  as "possibly relevant, not read", and can never support or challenge a candidate. It can make a candidate's status `INSUFFICIENT_COVERAGE` (below).
* Full texts are added by the researcher (`research corpus import`), who asserts the right to use them (section 12).

### Coverage reporting

Every `RetrievalRun` records: corpus digest (sorted `(source_id, revision, text_sha256)`), size (full-text / abstract-only / metadata-only), queries with
expansions, filters, hit counts per stage, researcher exclusions with reasons, external API calls (source, date, pages, records), and **known gaps**:
acquisition-list items not imported, year range covered, languages. The coverage block is copied into every challenge status (5.5) and into the export.

## 5.4 Candidates (discovery)

> **Amended (A1.1):** required fields add `core_claim`, `locus`, `base_claim`, `scope_reason`, `method`, `evidence_type`. Generation needs a corpus at or
> above the floor (A3); below it no candidate is generated.

**Discovery is separate from confirmation.** A generation step proposes; only 5.5 assesses. The generator never sees its own previous verdicts, and
the verifier does not see the generator's rationale beyond the candidate fields (to avoid self-confirmation).

Taxonomy (`candidate_type`): `extends_mechanism`, `challenges_finding`, `bounds_finding` (new boundary condition), `reconciles_contradiction`,
`new_measurement`, `new_identification`, `new_setting_with_theoretical_reason`, `new_setting_only` (flagged: weak unless a reason is given),
`method_transfer` (from another field, with transfer assumptions), `replication_with_variation`.

Required fields:

```jsonc
{
  "candidate_id": "CAND_0003", "task_id": "TASK_0001", "interpretation": "I1",
  "type": "bounds_finding",
  "claim": "…what the study would show…",
  "mechanism": "…why it would be so…",
  "gap": {"entries": ["LE_014", "LE_022"], "text": "…"},           // landscape entries it rests on (validated to exist)
  "what_would_have_to_be_true": ["…", "…"],                        // falsifiable preconditions
  "setting": {"population": "…", "period": "…", "unit": "…"},
  "expected_contribution_dimensions": ["population", "estimand"],  // which of the 10 dimensions (5.5) it claims to differ on
  "closest_known": ["SRC_ZAVITSANOS2025"],                          // from landscape, may be empty
  "generated_by": "model:deepseek-v4-pro", "generator_skill_digest": "…",
  "status": "PROPOSED"
}
```

Validation (static, before storage): every `gap.entries` id exists; `type` in vocabulary; `new_setting_only` without a reason flagged; duplicate
detection against existing candidates and **idea history** (prior attempts are listed *before* generation and passed to the generator as "already tried,
with status"; a regenerated pre-empted idea is linked, not stored as new).

## 5.5 Adversarial verification (confirmation)

> **Amended (M5_DESIGN_AMENDMENT_1.md A1, A2):** the ten dimensions below no longer decide the status. Each candidate states a `core_claim` and a
> `locus` (CLAIM / SCOPE / METHOD / EVIDENCE); each (candidate, competitor) pair is a `CompetitorAssessment` with facets `claim_rel`, `scope_rel`,
> `scope_reason`, `method_rel`, `evidence_rel`, `p_quality`; a 25-row truth table gives the pair verdict; the candidate status follows A1.4. HIGH =
> `claim_rel` always plus every facet that is *sensitive* under the truth table; negative relations are confirmed by researcher judgement, not ECA.
> The text below is kept for history; where it conflicts with A1–A2 it is superseded.

For each candidate and each competitor from R4 (top *n*, default 5, plus all researcher-added papers):

1. One model call per (candidate, competitor) with the candidate fields and the competitor's stored text sections (within context budget,
   `context.py` full-text policy): "State the strongest case that this paper already does what the candidate proposes, dimension by dimension, quoting the paper."
2. Each returned quote is verified (`quotes.find_quote`); unverifiable quotes are dropped and counted.
3. Each "the paper already does X on dimension D" becomes an ECA statement (HIGH) adjudicated against its passage (`eca.adjudicate`), and later
   confirmed by the researcher (D15: 100% of HIGH).

Ten dimensions: `research_question`, `mechanism`, `construct_definition`, `measurement`, `population`, `period`, `unit_of_analysis`, `estimand`,
`design_identification`, `data_source`. Per dimension the outcome is `SAME`, `OVERLAPPING`, `DIFFERENT`, `NOT_STATED_IN_SOURCE`, `NOT_ASSESSED`, each with
passages.

Candidate status (`challenge_status`), corpus-bounded:

| Status | Rule (applied deterministically to confirmed adjudications) |
|---|---|
| `CHALLENGED` | ≥1 competitor is `SAME` on every dimension the candidate claims to differ on, by confirmed `SUPPORTED_AS_STATED` adjudications |
| `PARTIALLY_ADDRESSED` | ≥1 competitor `SAME`/`OVERLAPPING` on some claimed dimensions; the remaining dimensions are listed as "what is left" |
| `NOT_CHALLENGED_IN_SEARCHED_CORPUS` | all competitors assessed; none `SAME`/`OVERLAPPING` on claimed dimensions; coverage block attached |
| `INSUFFICIENT_COVERAGE` | assessment ran but coverage is below the declared floor: competitors on the acquisition list without full text, or fewer than *m* full-text competitors retrieved, or a claimed dimension that every competitor leaves `NOT_STATED_IN_SOURCE` |
| `NOT_ASSESSED` | verification not run, or adjudications not yet confirmed |

**Evaluation of extra statuses (spec 5.5).** A separate `MISSING_FULL_TEXT` status is *not* recommended: missing full text is one cause of insufficient
coverage and is listed inside `INSUFFICIENT_COVERAGE.reasons` with the acquisition items; a separate status would let the export show
`NOT_CHALLENGED` for the papers held while hiding the unread ones. `INSUFFICIENT_COVERAGE` *is* recommended as its own status because it changes what the
researcher should do next (read more), whereas `NOT_CHALLENGED_IN_SEARCHED_CORPUS` says "the search you approved found nothing". Unconfirmed model
adjudications never move a status beyond `NOT_ASSESSED` (I3, I16). The word "novel" never appears in a status, a field or an export (a test greps the
renderers' output).

## 5.6 Feasibility

For candidates not `CHALLENGED` (the researcher may also request it for challenged ones):

* **Data**: what data the claim needs (unit, period, variables), stated by the candidate; matched against `Dataset` objects the project declares. Never
  inferred from the shape of data the project happens to have.
* **Measures and proxies**: from landscape `measurement` entries; each proxy with its source passage and known criticisms.
* **Identification / validation assumptions**: listed; each `SATISFIED / NOT_SATISFIED / UNKNOWN (fact needed)` via `playbooks.evaluate_conditions`.
* **Playbooks**: `playbooks.select_playbooks(context)` gives applicable / candidate / related; a playbook is reported as **candidate, validity not
  established here** unless `lifecycle.match` finds a validity assessment for this context (existing rule, `docs/ANALYSIS_PIPELINE.md`).
* **Journal alignment** if the researcher names a journal (`journal_policy.alignment`).
* Output status per candidate: `FEASIBLE_IF` (all conditions SATISFIED or UNKNOWN with listed facts), `NOT_FEASIBLE_WITH_DECLARED_DATA`, `NOT_ASSESSED`.
  Never "feasible" without conditions.

## 5.7 Researcher decisions

A `ResearchDecision` object per action:

```jsonc
{"decides": {"object_id": "CAND_0003", "revision": 2}, "action": "narrow_claim",
 "rationale": "…", "new_candidate": "CAND_0003@3", "with": [],
 "revisit_conditions": [{"when": "results_change", "object": "RES_0004"}],
 "decided_by": "<actor id>", "generated_by": "researcher"}
```

Actions: `keep`, `drop`, `merge` (with), `split` (into), `change_mechanism`, `narrow_claim`, `read_more` (adds acquisition items, re-opens R5),
`go_to_methods` (continues the task with goal `evaluate_methods`), `revisit_when_results_change` (binds to a Result; `graph/dependency.py` marks the
decision `NEEDS_REVIEW` when that Result is revised).

Rules: a decision needs `author` or `owner` role; a rationale is mandatory; there is no default action and no "accept all" (as `eca.confirm_batch`);
an action that changes a claim (`narrow_claim`, `change_mechanism`, `merge`, `split`) creates a **new candidate revision written by the researcher**
— the suite never applies a suggested rewording itself; the new revision has status `NOT_ASSESSED` until re-verified (statements bound to text digests, as in ECA).

## Pseudocode: one discovery task

```text
task = start(goal=discover_contribution, problem=RP)
plan = planner(goal, facts(project), library.match(problem.scope))       # lists prior attempts first
approve(plan)                                                            # researcher
for step in plan.steps in dependency order:
    if step.kind == decide: wait for ResearchDecision bound to current revisions
    if budget.spent + estimate(step) > budget.max: BLOCK(step, numbers)
    fp = fingerprint(step, input pins, skill digest, model)
    if run = registry.latest_reusable(step.capability, fp): reuse(run); continue
    out = capability(step.inputs)                  # proposals only; quotes verified; ECA statements created HIGH
    register(run, task_id, step_id, usage, out pins)
statuses = derive_challenge_status(confirmed adjudications, coverage)   # deterministic
export(task)                                                              # section 8 of the main plan
```
