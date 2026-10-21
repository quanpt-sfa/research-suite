# M5 Design Amendment 1 — response to the design review of 2026-10-21

> **Partly superseded by `M5_DESIGN_AMENDMENT_2.md` (second review, 2026-10-22)** — in particular §A1.2–A1.5 (the 25-row table is replaced by the
> claim-level precedence table in `M5_WP14_TRUTH_TABLE_DRAFT.md`), §A2.1, §A2.3, §A3, §A4.3, §A6 (MA), §A7, §A8, §A10, §A11. See Amendment 2 §B13.

Status: **design proposal, not implemented.** This amendment changes the nine M5 documents where they conflict with it; where it is silent, they stand.
Each superseded passage in those documents carries a pointer to the section here. Code references: `main` = `c6397de`.

Approval scope requested after this amendment:

| Scope | Requested status |
|---|---|
| WP0 CI repair | ready (review: "có thể triển khai") — still waits for the owner's explicit go |
| WP1 foundations, WP2 exporter | design closed by A7 (transactions), A8 (budget), A9 (export); ready for approval |
| WP3–WP13 | **not** requested yet: approval after the owner accepts A1–A6 and the fixtures in A10 |

---

## 0. Analysis of the review

| # | Review point | Verdict | Evidence checked | Change |
|---|---|---|---|---|
| R1 | The `CHALLENGED` rule compares ten attributes as equals; it can mark a different mechanism as challenged and a copied mechanism in a new market as only partial | **agree** | discovery protocol §5.5 status table | A1: contribution structure (core claim, locus, scope, method, evidence) and a truth table at the (candidate, competitor) level |
| R2 | ECA "100% HIGH" contradicts sampling; HIGH statements cannot be known before checking; sampling may block `NOT_CHALLENGED` | **agree**; one further gap found: a negative relation ("P does not address X") has no passage, so ECA, which checks a statement against a passage, cannot check it | `eca.adjudicate(statement, passage_id)` takes one passage | A2: decision unit = (candidate, competitor, core claim); sensitivity computed from the truth table; negative relations confirmed by the researcher with the closest passages shown; workload table |
| R3 | Cold-start Test A contradicts `CORPUS_EMPTY → BLOCKED` | **agree** | Gate M §3 Test A vs WP5 errors | A3: two (three) cold-start cases with distinct expected outputs |
| R4 | A new paper may not re-open a `NOT_CHALLENGED` status if it misses top-k | **agree**; large deltas need an honest limit | knowledge/integration §9.2 row 1–2 | A4: corpus snapshots; coverage-bound statuses become `STALE_FOR_CORPUS`; exhaustive delta screening for small deltas, declared prefilter above a threshold |
| R5 | `evidence_permits` filled after ECA could read as "the evidence supports it" | **agree**; refinement: reproduction and critical assessment are separate, not a ladder (reproducing flawed code reproduces the flaw) | discovery protocol §5.2 | A5: three separate evidence dimensions for findings, mirroring the library's three validity dimensions |
| R6 | Gate M tests critique of given candidates, not discovery of good ones; false pre-emption not measured | **agree** | Gate M B–D use a scripted generator | A6: Gate M split into framing, candidate quality (blind, against a baseline), competitor retrieval, assessment accuracy (both error directions), usefulness |
| R7 | Atomicity across transactions not shown | **agree, and the problem already exists**: `reading_protocols.run_protocol` stores passages (`_store_records`, one transaction) and then registers the run (`_register`, another); `eca.adjudicate` registers a SUCCEEDED run and *then* writes the record (`eca.py` lines 375–379 of `adjudicate`) | read of both functions | A7: stage-then-commit, one commit transaction per unit of work, orphan detection; WP1 fixes the two existing sites |
| R8 | Budget check is on estimates, not a hard limit | **agree** | data-model §7 | A8: reservation per call with `max_output_tokens` capped by the remaining budget; conservative charging; task lease |
| R9 | Export: refusing `sample/` contradicts where results live; timestamp vs reproducibility; global `state_sha256` changes for unrelated writes | **agree** | export contract §1 | A9: path rule by store directories and the repository tree; deterministic `manifest.json` vs variable `export_info.json`; task closure digest |
| R10 | Real-world adversarial corpus needed (many papers, terminology drift, competing findings, held-out competitor) | **agree**; depends on papers the owner may use | — | A10 fixtures + new decision D23 |
| R11 | CI numbers | **review correct**: 3.11, 3.12, 3.12+tiktoken: `1 failed, 680 passed, 6 skipped`; 3.10: `1 failed, 679 passed, 7 skipped` (run 39576120896). `M5_AUDIT.md` quoted the 3.10 line only | CI log | audit corrected |

What the review keeps and this amendment does not touch: `ResearchProblem` apart from `ResearchQuestion`; candidates in the project, idea history in the
library; no parallel store; progressive retrieval with metadata-only never evidence; decisions with revision and rationale; domain workflows as execution
contracts; synthetic separated from scientific evaluation; exports for all five workflows; history of rejected ideas with reopen conditions.

---

## A1. Contribution structure and pre-emption logic (replaces discovery protocol §5.4 required fields in part and §5.5 status rules)

### A1.1 What a candidate must state

A candidate's contribution is not a list of attributes; it is one **core claim** plus the place where the candidate says its contribution lies (the **locus**):

| Field | Content | Example |
|---|---|---|
| `core_claim` | the proposition the study would add, as one sentence | "Managers who learn that detection models use accrual signals shift manipulation to real activities" |
| `locus` | `CLAIM` (new mechanism / theoretical proposition / finding), `SCOPE` (where a known claim holds or fails), `METHOD` (new measurement or identification of a known question), `EVIDENCE` (a new kind of evidence for a known claim: causal design, replication, new data type) | `CLAIM` |
| `base_claim` | for SCOPE / METHOD / EVIDENCE: the known claim the contribution refers to | — |
| `scope` | population, period, market, unit, conditions | US listed firms, 2004–2018 |
| `scope_reason` | optional: why the claim is expected to differ in this scope (a theoretical reason with its basis) | — |
| `method` | measurement and identification the study would use | — |
| `evidence_type` | `descriptive`, `predictive`, `correlational`, `quasi_experimental`, `experimental`, `qualitative`, `conceptual`, `replication` | `quasi_experimental` |

The four groups of the review map onto this: core scientific claim = `core_claim` (+`base_claim`); scope and boundary = `scope`, `scope_reason`;
methodological differentiation = `method`; evidentiary differentiation = `evidence_type`. The ten dimensions of the earlier draft remain as descriptive
fields of the competitor comparison (exported), but **only the facets below enter the verdict**.

### A1.2 Facets assessed per (candidate X, competitor P)

| Facet | Values | Meaning |
|---|---|---|
| `claim_rel` | `E` establishes, `PE` partly establishes, `OPP` addresses it with a contrary answer, `REL` related but different claim, `NA` not addressed, `U` cannot be determined from the text held | relation of P to X's `core_claim` (locus CLAIM) or to X's `base_claim` (other loci) |
| `scope_rel` | `COV` P's scope covers X's, `OVL` overlaps, `DIS` disjoint, `NS` not stated | |
| `scope_reason` | `R` confirmed reason, `NR` none, `UNCONF` stated but not confirmed | property of X, not of the pair |
| `method_rel` | `SAME`, `DIFF` | P used X's method for the same question |
| `evidence_rel` | `SAME_TYPE`, `NEW_TYPE` | P already gave evidence of X's type for the claim |
| `p_quality` | `OK` (not assessed or no confirmed flaw), `FLAWED` (confirmed method assessment `INFERENCE_NOT_SUPPORTED` or `INFERENCE_LIMITED`, A5) | |

### A1.3 Pair verdict truth table (first matching row wins; deterministic, pure function)

Verdicts: `PREEMPTS`, `PARTIALLY_PREEMPTS` (with `left`), `CONTESTS`, `BUILDS_ON`, `RELATED`, `UNRELATED`, `UNDETERMINED`.

| # | Locus | Condition | Verdict | `left` / suggestion |
|---|---|---|---|---|
| 1 | any | `claim_rel = U` | UNDETERMINED | reason: what was missing in P's text |
| 2 | any | `claim_rel = NA` | UNRELATED | — |
| 3 | any | `claim_rel = REL` | RELATED | — |
| 4 | any | `claim_rel = OPP` | CONTESTS | suggestion: `reconciles_contradiction`; never a pre-emption |
| 5 | CLAIM | `E`, `p_quality = FLAWED` | PARTIALLY_PREEMPTS | left: a credible test (evidentiary); suggest locus EVIDENCE |
| 6 | CLAIM | `E`, `evidence_rel = NEW_TYPE` | PARTIALLY_PREEMPTS | left: evidentiary contribution only |
| 7 | CLAIM | `E`, `scope_reason ≠ R` | **PREEMPTS** | a different market without a reason is not a new claim |
| 8 | CLAIM | `E`, `scope_reason = R`, `scope_rel = COV` | PREEMPTS | — |
| 9 | CLAIM | `E`, `scope_reason = R`, `scope_rel ∈ {OVL, DIS}` | PARTIALLY_PREEMPTS | left: the boundary test; suggest locus SCOPE |
| 10 | CLAIM | `E`, `scope_reason = R`, `scope_rel = NS` | UNDETERMINED | reason: P's scope not stated |
| 11 | CLAIM | `PE` | PARTIALLY_PREEMPTS | left: the part of the claim P does not establish (stated in the assessment) |
| 12 | SCOPE | `E/PE`, `scope_rel = COV` | PREEMPTS | — |
| 13 | SCOPE | `E/PE`, `scope_rel = OVL` | PARTIALLY_PREEMPTS | left: the non-overlapping part of the scope |
| 14 | SCOPE | `E/PE`, `scope_rel = DIS` | BUILDS_ON | P is the base finding |
| 15 | SCOPE | `E/PE`, `scope_rel = NS` | UNDETERMINED | — |
| 16 | METHOD | `E/PE`, `method_rel = DIFF` | BUILDS_ON | the candidate must say why its method changes what is known (`what_would_have_to_be_true`) |
| 17 | METHOD | `E/PE`, `SAME`, `p_quality = FLAWED` | PARTIALLY_PREEMPTS | left: a correct application of the method |
| 18 | METHOD | `E/PE`, `SAME`, (`scope_reason ≠ R` or `scope_rel = COV`) | PREEMPTS | — |
| 19 | METHOD | `E/PE`, `SAME`, `scope_reason = R`, `scope_rel ∈ {OVL, DIS}` | PARTIALLY_PREEMPTS | left: the method in the new scope |
| 20 | METHOD | `E/PE`, `SAME`, `scope_reason = R`, `scope_rel = NS` | UNDETERMINED | P's scope not stated |
| 21 | EVIDENCE | `E/PE`, `evidence_rel = NEW_TYPE` | BUILDS_ON | — |
| 22 | EVIDENCE | `E/PE`, `SAME_TYPE`, `p_quality = FLAWED` | PARTIALLY_PREEMPTS | left: a credible version |
| 23 | EVIDENCE | `E/PE`, `SAME_TYPE`, (`scope_reason ≠ R` or `scope_rel = COV`) | PREEMPTS | — |
| 24 | EVIDENCE | `E/PE`, `SAME_TYPE`, `scope_reason = R`, `scope_rel ∈ {OVL, DIS}` | PARTIALLY_PREEMPTS | left: evidence in the new scope |
| 25 | EVIDENCE | `E/PE`, `SAME_TYPE`, `scope_reason = R`, `scope_rel = NS` | UNDETERMINED | P's scope not stated |

Note on rows 5–11 for locus CLAIM: `method_rel` does not enter (a new method for an already established claim is a METHOD-locus contribution; the
suggestion column proposes restating the candidate). Rows 12–15 (SCOPE) do not use `p_quality`: a flawed test of the claim in a covering scope is
recorded as a flag on the pair (`p_quality: FLAWED`) and shown, but the verdict stays as in the table; the owner may decide in review of WP14 to add a
row analogous to row 5.

`scope_reason = UNCONF` is evaluated both as `R` and as `NR`; if the verdicts differ, the scope-reason statement is *sensitive* (A2) and must be confirmed.
A table-completeness test enumerates all combinations and asserts every one matches exactly one first row.

### A1.4 Candidate status (aggregation over pairs)

```text
def candidate_status(X, pairs, coverage):            # pairs: assessments bound to X's current revision and corpus snapshot
    confirmed   = [p for p in pairs if p.confirmed]  # researcher-confirmed (A2)
    unconfirmed = [p for p in pairs if not p.confirmed]
    flags = {"contested_by": [p.competitor for p in confirmed if p.verdict == CONTESTS]}
    if any(p.verdict == PREEMPTS for p in confirmed):
        return CHALLENGED, flags                       # sufficient: one confirmed pre-emption; later confirmations cannot undo it
    if any(p.verdict == PARTIALLY_PREEMPTS for p in confirmed):
        return PARTIALLY_ADDRESSED(left = union(p.left), unconfirmed = len(unconfirmed)), flags
    if unconfirmed:
        return NOT_ASSESSED(confirmed = len(confirmed), of = len(pairs)), flags
    if coverage.below_floor or any(p.verdict == UNDETERMINED for p in confirmed):
        return INSUFFICIENT_COVERAGE(reasons), flags
    return NOT_CHALLENGED_IN_SEARCHED_CORPUS(snapshot = coverage.snapshot_id), flags
```

* `CONTESTS` never changes the status; it is shown as a flag because competing findings change what the candidate must argue.
* `PARTIALLY_ADDRESSED` with unconfirmed pairs is displayed as "at least partially addressed; *n* pairs unconfirmed" (it can still become CHALLENGED).
* `BUILDS_ON`, `RELATED`, `UNRELATED` are listed in `closest_competitors.csv`; they matter for the landscape, not for the status.
* The model's *proposed* verdicts produce a `provisional_reading` shown in a separate column labelled `model_synthesised`; it never fills the status column.

### A1.5 Why this fixes the two review examples (fixtures F1, F2 in A10)

* Same population and period, different mechanism: `claim_rel = REL` → row 3 → RELATED; old rule: CHALLENGED (false pre-emption).
* Same mechanism in another market, no reason given why the market matters: row 7 → PREEMPTS → CHALLENGED; old rule: PARTIALLY_ADDRESSED (missed
  pre-emption). With a confirmed reason: row 9 → PARTIALLY_PREEMPTS with the boundary test left, and a suggestion to restate the candidate with locus SCOPE.

---

## A2. Decision granularity and verification workload (replaces discovery protocol §5.5 steps 3 and the ECA tiering in Gate M doc §6)

### A2.1 Unit of decision

A `CompetitorAssessment` object per (candidate revision, competitor source revision, corpus snapshot): the facets of A1.2, each as a **facet statement**
with its basis, the derived pair verdict, the model descriptor, and `confirmed_by` once the researcher confirms it. The researcher confirms **the
assessment** (one row in the sheet), giving a verdict per *sensitive* facet and one rationale.

How each facet is checked depends on its form:

| Facet statement form | Check | Who decides |
|---|---|---|
| positive, with a passage ("P establishes the claim: '…'") | quote verification + ECA (`eca.adjudicate`, HIGH) | researcher confirms the ECA proposal |
| negative, no passage can exist ("P does not address the claim") | no ECA (there is nothing to read the statement against). The sheet shows the *closest passages* of P to the claim (in-document BM25 over P's sections) and P's abstract/introduction locators | researcher judgement (`assessment_kind: scientific_judgment`, existing vocabulary in `protocols/schema.py` Adjudication) |
| scope/method/evidence relation with passages on both sides | ECA on P's passage; X's side is the candidate's own field | researcher |
| `scope_reason` (property of X) | ECA against its theory passage if it cites one; otherwise researcher judgement | researcher |

### A2.2 Which facets must be checked: sensitivity, not a guess

A facet is **sensitive** for a pair if replacing its proposed value by any other admissible value changes the pair verdict (A1.3), holding the other
facets at their current values (confirmed if confirmed, proposed otherwise). Sensitive facets are HIGH; `claim_rel` is always HIGH (it decides whether
the pair matters at all, and a wrong `NA` hides a pre-emption — the review's failure case). Non-sensitive facets cannot change any status by
construction; they are sampled at the D15 rate only to measure the quality of the descriptive export, never to decide anything.

```text
def required_checks(assessment):
    while True:
        values = {f: (f.confirmed_value or f.proposed_value) for f in assessment.facets}
        sensitive = {"claim_rel"} | {f for f in facets if any(verdict(values | {f: v}) != verdict(values) for v in domain(f))}
        new = sensitive - assessment.required
        if not new: return assessment.required
        assessment.required |= new            # e.g. the researcher corrects claim_rel NA -> E: scope_rel becomes sensitive
```

The loop is re-run after every confirmation that changes a value; it terminates because facets and domains are finite and `required` only grows.
This removes the circularity the review identified: no status is computed from unchecked sensitive facets, and the set of sensitive facets is known
from the truth table, not from the outcome.

### A2.3 Workload: worked scenarios (4 candidates × 5 competitors = 20 pairs)

Assumptions stated: one adversarial call per pair produces all facets; ECA is one call per positive HIGH facet statement; non-sensitive positive
facets sampled at 20%; negative facets cost no model call.

| Scenario | Pairs with positive `claim_rel` (E/PE/OPP) | Sensitive positive facets besides `claim_rel` | Model calls (adversarial + ECA HIGH + ECA sample) | Researcher rows (pair assessments) | Researcher facet verdicts | Candidate decisions |
|---|---|---|---|---|---|---|
| S1 typical | 6 | 8 | 20 + (6 + 8) + ~3 = **~37** | 20 | 20 `claim_rel` + 8 = 28 | 4 |
| S2 crowded field | 20 | 30 | 20 + 50 + ~6 = **~76** | 20 | 50 | 4 |
| S3 sparse field | 1 | 1 | 20 + 2 + ~1 = **~23** | 20 | 21 | 4 |
| S4 correction cascade (S1, researcher corrects 2 `NA → E`) | 8 | 12 | S1 + 2 (ECA on the corrected claims) + 4 (newly sensitive facets) = **~43** | 20 (2 reopened) | 34 | 4 |

The earlier plan quoted ~52 ECA calls for 200 dimension statements; S1 needs ~17 ECA calls because the unit is the pair, not the attribute, and because
negative relations are judged by the researcher rather than adjudicated against non-existent passages. The researcher's load is the 20 assessment
rows; this is the real cost of a reliable `NOT_CHALLENGED`, and the plan says so instead of hiding it in sampling.

### A2.4 Status as confirmations arrive (S1)

| Confirmed pairs | Example state | C1 status | C2 status |
|---|---|---|---|
| 0 / 20 | all proposals | NOT_ASSESSED (0/5) | NOT_ASSESSED (0/5) |
| 6 / 20 | C2–P1 PREEMPTS confirmed | NOT_ASSESSED (1/5) | **CHALLENGED** (final; further rows of C2 optional and marked so) |
| 15 / 20 | C1: 4 confirmed, all UNRELATED/RELATED | NOT_ASSESSED (4/5) | CHALLENGED |
| 20 / 20 | C1 all confirmed, coverage ≥ floor | NOT_CHALLENGED_IN_SEARCHED_CORPUS (snapshot S1) | CHALLENGED |
| 20 / 20 | as above but P4 lacked a method section (`U`) | INSUFFICIENT_COVERAGE (P4 text incomplete) | CHALLENGED |

Rows of an already CHALLENGED candidate are moved to the end of the sheet as optional; skipping them is allowed and recorded.

---

## A3. Cold start (replaces Gate M Test A; amends WP5 errors)

| Case | Setup | Expected outputs | Expected task state | Model calls | Must not happen |
|---|---|---|---|---|---|
| **A1** no RQ / Introduction / manuscript, corpus present (≥ floor) | problem statement; ≥ `min_fulltext_sources` relevant full texts (default 3) | full bundle; candidates with statuses | WAITING at framing, then at candidate decisions | per plan | any step skipping a checkpoint |
| **A2** no corpus, no library, metadata off | problem statement only | `ResearchProblem` (+ interpretations if requested), `search_plan.md` (queries, interpretation terms, what kinds of papers are needed), `missing_inputs.md`; all candidate files written with `NOT_AVAILABLE: corpus below floor (0 full texts)` | **BLOCKED** at `retrieve`, reason `CORPUS_BELOW_FLOOR` | framing only (0 if not requested) | any candidate generated; any status other than absent |
| **A3** no corpus, metadata on | problem statement; recorded OpenAlex/Crossref responses | as A2 plus `acquisition_list.csv` (metadata only, dedup by DOI) | BLOCKED at `retrieve`, reason `CORPUS_BELOW_FLOOR`, with the acquisition list | framing + expansion | candidates generated from abstracts; any metadata record cited as evidence |
| **A4** corpus below floor (1–2 full texts) | as A1 with 2 papers | as A2; the planner offers `--accept-small-corpus`, which the researcher may set with a rationale; then statuses can only be `INSUFFICIENT_COVERAGE` or `CHALLENGED`/`PARTIALLY_ADDRESSED` | BLOCKED until the flag or more papers | — | `NOT_CHALLENGED_IN_SEARCHED_CORPUS` below the floor |

The floor (`min_fulltext_sources`) and the relevance condition (a source counts if it is retrieved for at least one interpretation at R1/R2) are
declared in the goal spec and recorded in every coverage block. The pilot corpus (2–3 papers) is case A4: Gate M real either imports more papers
(D23) or runs with `--accept-small-corpus` and can then only test pre-emption detection, never `NOT_CHALLENGED`.

---

## A4. Corpus snapshots and coverage-dependent invalidation (replaces knowledge/integration §9.2 rows 1–2)

### A4.1 Invariant

> **I20 — Absence is relative to a corpus snapshot.** Every conclusion that rests on *not finding* something (`NOT_CHALLENGED_IN_SEARCHED_CORPUS`,
> `PARTIALLY_ADDRESSED.left`, `INSUFFICIENT_COVERAGE`, `UNRELATED` and `RELATED` pair verdicts used for those) is bound to a `corpus_snapshot_id`.
> When the project's current snapshot differs, the conclusion is kept as history and presented as `STALE_FOR_CORPUS` until the delta has been screened.

`corpus_snapshot_id = sha256(sorted (source_id, revision, text_sha256, fulltext_status) of all sources in the project's corpus, + exclusions with reasons)`.
Adding, revising (an erratum), excluding or re-including a source changes it.

### A4.2 States of a coverage-bound status

```
        (all pairs confirmed at snapshot S)
CURRENT@S ──corpus changes to S'──► STALE_FOR_CORPUS(S → S', delta)
    ▲                                   │ delta screening
    │                                   ├─ no delta source relevant (confirmed) ─► CURRENT@S' (same status, new snapshot, screening record)
    └──── re-derived ◄──────────────────┴─ ≥1 delta source relevant ─► new CompetitorAssessments ─► status re-derived at S'
CHALLENGED@S: stays CHALLENGED on corpus growth (a pre-emption cannot be undone by more papers); it becomes STALE only if the pre-empting
source is revised or excluded (its passages are re-verified by the existing digest rules).
```

### A4.3 Delta screening (does not depend on top-k)

```text
def screen_delta(task, S_old, S_new):
    delta = sources(S_new) - sources(S_old)             # new or revised full texts
    for X in candidates(task) with coverage-bound status:
        if len(delta) <= DELTA_EXHAUSTIVE_MAX (default 30):
            for P in delta:                             # every new source, whatever its BM25 rank
                screen(X, P)                            # one model call: is P about X's core/base claim? proposal RELEVANT / NOT_RELEVANT with passages
        else:
            ranked = R1+R2 retrieval of X's queries over delta with a permissive cut (declared, benchmarked recall)
            for P in ranked: screen(X, P)
            record coverage.delta_method = "prefilter", with its benchmark recall
    researcher confirms each NOT_RELEVANT screen in the sheet (one row per (X, P); no default)
    RELEVANT -> full CompetitorAssessment (A2)
```

Cost of a small delta: `|delta| × |open candidates|` screening calls (e.g. 3 new papers × 3 candidates = 9 calls) plus assessments for the relevant
ones. Above the threshold the method is a declared prefilter and the coverage block says so: the plan does not claim exhaustive screening it cannot afford.

### A4.4 Other changes that re-open statuses

| Change | Effect |
|---|---|
| candidate revised (narrowed, mechanism changed) | all its assessments are bound to the old revision: status NOT_ASSESSED for the new revision; old kept |
| competitor source revised (erratum) | assessments on it → re-check passages (existing digest binding); its verdict NOT_ASSESSED until re-confirmed |
| method assessment of a competitor changes `p_quality` | pairs whose verdict depends on `p_quality` (rows 5, 17, 22) re-derived; if a confirmed verdict changes, the assessment is reopened |
| `scope_reason` confirmed or rejected | all pairs of that candidate re-derived; newly sensitive facets queued (A2.2) |

---

## A5. Evidence dimensions for findings (replaces `evidence_permits` in discovery protocol §5.2)

`evidence_permits` is withdrawn. Each landscape `finding` (and each competitor's establishment of a claim) carries three separate dimensions, the same
separation the library already uses for knowledge (`lifecycle.py`: source fidelity, computational reproducibility, methodological validity):

| Dimension | Values | Requires | Does **not** follow from |
|---|---|---|---|
| `reading` (what the author reports) | `AUTHOR_REPORTED` (quote-verified), `READING_CONFIRMED` (ECA confirmed: the suite's statement is a fair reading of the passage) | passage; ECA | — |
| `inference` (does the design permit the inference) | `NOT_ASSESSED`, `METHOD_CRITICALLY_ASSESSED: SUPPORTED_WITHIN_SCOPE / LIMITED / NOT_SUPPORTED` | a method assessment record: criteria with their methodological-literature basis (same rule as `assess_validity`), threats checked, code evidence where available (file sha + line range of a replication package), researcher confirmation | ECA (a correct reading of a wrong claim is still wrong) |
| `reproduction` | `NOT_ATTEMPTED`, `REPRODUCED_WITHIN_TOLERANCE`, `FAILED_TO_REPRODUCE`, `REPLICATED_NEW_DATA` | replication package run (`replication.reproduce_package`) or a new-data replication record | inference assessment (reproducing leaked code reproduces the leak) |

Display rule: a finding is never shown as "established" or "supported"; the export shows the three values side by side. Use in A1: `p_quality = FLAWED`
only from a confirmed `inference: LIMITED` or `NOT_SUPPORTED` (rows 5, 17, 22 of A1.3). Example from the pilot: the Gate K paper-code differences (D1–D7 in the researcher's file)
are the kind of evidence an inference assessment records; until they are adjudicated, Zavitsanos 2025 findings stay `AUTHOR_REPORTED` / `NOT_ASSESSED`
/ `REPRODUCED_WITHIN_TOLERANCE` (PU-HRF metrics reproduced on 12 splits), which the export shows exactly so.

---

## A6. Gate M revised (replaces Gate M doc §2–§4)

Gate M now has five components. Each is reported separately; there is no single score.

| Component | Question | Synthetic (CI) | Real (owner) | Metric | Pass rule (proposed; the owner declares thresholds before the run) |
|---|---|---|---|---|---|
| **MF framing** | does the suite see several plausible readings of the problem? | only mechanics (interpretations stored as PROPOSED, researcher chooses) | blind reference list of interpretations written first | coverage of reference interpretations (researcher matches); count of additional interpretations rated plausible | coverage ≥ declared threshold (proposed 0.5); descriptive otherwise |
| **MG candidate quality** | are candidates worth checking, beyond what direct prompting gives? | mechanics only: candidates reference existing gap entries, `new_setting_only` flagged; **no quality claim** | blind rating against a baseline (A6.1) | rubric medians, share rated "worth checking" | suite median ≥ baseline median on *grounded* and *specific*; share worth checking ≥ declared threshold |
| **MR competitor retrieval** | is the competing paper found, including under other terminology? | fixtures F12, F14 | reference competitors per candidate, written blind; held-out competitor | recall@k of reference competitors; rank of the held-out paper after insertion | recall@k ≥ declared threshold; held-out paper screened (A4) in 100% of cases |
| **MA assessment accuracy** | are pre-emptions found and **non-pre-emptions not flagged**? | truth-table fixtures F1–F13 (exact expected verdicts) | blind reference verdicts per (candidate, competitor) | confusion matrix of *proposed* pair verdicts vs reference: missed pre-emption rate, **false pre-emption rate**; same for final statuses after confirmation | proposals: missed ≤ declared (proposed 0.2), false pre-emption ≤ declared (proposed 0.2); final statuses: 0 missed in-corpus pre-emptions among reference items |
| **MU usefulness** | can the researcher make a reasoned decision from the bundle? | export completeness, traceability | for each candidate decision: rationale cites report items (traceability check), time to decide (self-recorded), rubric of 4 questions | share of decisions whose rationale cites the bundle; rubric | ≥ declared share (proposed 0.8); rubric descriptive |

Plus the process tests that stay binary: cold start (A3: A1–A4), revisit (old G), human override (old H), reproducibility (old I, with A9 fields),
cumulative reuse (old J), **held-out competitor (new K)**, crash recovery (new L, A7), hard budget (new N, A8).

### A6.1 Candidate quality without self-grading

* Baseline B0: the same model, given only the problem statement, asked for candidates in one call (no retrieval, no landscape). Optional B1: the
  researcher's own candidates written before the run.
* All candidates are rendered in one neutral template (core claim, locus, mechanism, scope; ids, gap references and generator removed), shuffled,
  and rated by the researcher on a declared rubric (1–4): **grounded** (the researcher can name the gap in the literature), **specific** (mechanism and
  scope stated), **testable**, **non-trivial**, **would check further** (yes/no). A second reader is optional.
* No model rates anything. The rating is done before the researcher sees statuses.
* Limits stated in the report: one rater, small *n*, so the comparison is descriptive; it can show that the pipeline is not worse than direct prompting
  on grounding, not that it is better in general.

### A6.2 Held-out competitor test (K)

Synthetic fixture and real protocol: one competing paper P\* that pre-empts (or partly pre-empts) a reference candidate is withheld from the corpus
for run 1. Expected: run 1 status for that candidate ≠ CHALLENGED and bound to snapshot S1; after importing P\*, the status shows `STALE_FOR_CORPUS
(S1 → S2, +1 source)`; delta screening screens P\* even though (in the synthetic fixture) P\* is constructed to rank below `k` for the candidate's R1
query; the new assessment yields the reference verdict; history keeps the S1 status. In the real protocol, the owner chooses P\* and records the
choice in the sealed reference before run 1.

---

## A7. Transactions, idempotency and crash recovery (amends data-model §4; foundation for WP1)

### A7.1 Rule: stage, then commit once

* A **unit of work** (UoW) is the smallest piece that is reused as a whole: one extraction of one source with one protocol; one ECA adjudication; one
  `CompetitorAssessment`; one screening; one task-plan revision. Multi-call steps are sequences of UoWs.
* Model calls run **outside** any write transaction. Each answer is first written to CAS together with an **attempt manifest** (`uow_key`, attempt
  number, model descriptor, usage, answer sha256). CAS writes change no scientific state.
* One **commit transaction** per UoW writes, together: the scientific objects, their edges, the run row (with `artifact_sha256`, usage, `task_id`,
  `task_step_id`), and the audit row. Either all exist or none.
* **Idempotency key** `uow_key = sha256(capability, version, canonical inputs, input pins, skill/protocol digest, model descriptor)`; the commit first
  checks `latest_reusable(uow_key)`: if a SUCCEEDED row appeared meanwhile, it commits nothing and returns the existing run (`reused_from_run_id`).
* Every object written by a UoW carries `produced_by_run`. An object whose `produced_by_run` has no registry row is an **orphan**: excluded from reuse,
  context, statuses and exports; listed by `research doctor`.

### A7.2 The two existing sites that violate it (fixed in WP1)

| Site | Today | Consequence of a crash between the two writes | Fix |
|---|---|---|---|
| `reading_protocols.run_protocol` | `_store_records` (tx 1) then `_register` (tx 2) | passages exist without a run; next run finds them through `_cached_from_passages` and registers them as if *imported*: no double cost, but usage and rejected records are lost and the provenance is wrong | one commit transaction (passages + run row) |
| `eca.adjudicate` | `_register` SUCCEEDED (tx 1) then `_write_record` (tx 2) | a SUCCEEDED run without an adjudication record; reuse may return a run whose record does not exist | one commit transaction; a test that reuse requires the record |

### A7.3 Crash points and expected recovery

| Crash point | State left | On retry | Model cost lost |
|---|---|---|---|
| C1 before the model call | nothing | normal run | 0 |
| C2 during/after the call, before CAS staging | nothing | call repeated | 1 call (unavoidable) |
| C3 after staging, before commit | attempt manifest in CAS | answer **re-used from the staged manifest** (same `uow_key`, model descriptor equal), no call | 0 |
| C4 inside the commit transaction | SQLite rollback: nothing | as C3 | 0 |
| C5 after commit, before the runner moves on | complete UoW | runner derives state from the registry, skips the UoW | 0 |
| C6 two runners on the same task | second fails to take the task lease (A8.3) | waits or exits | 0 |

Tests: fault injection at each point with the existing hook (`Tx.fault`, env `RESEARCH_TEST_FAULT`) plus a new pre-commit hook outside the transaction;
assert no duplicate objects, no orphan visible, registry consistent, and the staged answer reused at C3.

---

## A8. Hard budget (replaces data-model §7 enforcement)

### A8.1 Reservation per call

```text
before each model call:
    in_tok  = count_tokens(prompt)  (tiktoken if installed, else ceil(chars / 2.5) as an upper bound; method recorded)
    out_cap = min(adapter_ceiling, remaining_output_budget - safety_margin)
    if out_cap < step.min_useful_output or reserve(in_tok + out_cap) > remaining_total: BLOCK(step, numbers)   # nothing called
    call with max_output_tokens = out_cap
after the call:
    charged = provider usage if reported, else the reservation in full (in_tok + out_cap), flagged usage_source="reserved"
    retries inside the adapter (429/5xx) are each charged at their reservation unless the provider reports otherwise
    if spent >= 0.9 × budget: stop scheduling new UoWs; task BLOCKED (BUDGET_NEAR_LIMIT) with the numbers
```

* Because every call's output is capped by what remains, **actual spend cannot exceed the budget** except by the error of the input-token upper bound,
  which is conservative by construction. A `length` stop (truncation) is a failed run, still charged.
* Reasoning models count thinking in output tokens; the cap covers it (the OpenAI-compatible adapter already passes a ceiling, 64,000 by default).
* Money budgets need a price table; unknown cache discounts are charged at the full price.

### A8.2 Reporting

`cost_report.md`: reserved, charged, provider-reported, and the difference; calls refused for budget; estimate vs actual per step.

### A8.3 Task lease

`task run` takes a lease (`lease: {holder, acquired_at, expires_at}`) written as a task revision with `expected_head`; a second runner fails with
`TASK_LEASED`. An expired lease can be taken over; the previous runner's staged answers are reused (A7.3 C3). The budget ledger is the registry plus
the lease holder's in-memory reservations, so there is one writer of reservations at a time.

---

## A9. Export contract corrections (amends export contract §1)

| Issue | Correction |
|---|---|
| refusing `sample/` contradicts where evaluation results live | the rule is about **stores and the repository**, not about `sample/`: refuse any path inside a directory that contains `state.sqlite3` (a project or library store) or its `artifacts/`, inside `.git`, or inside the repository's tracked tree unless `--into-repo` is given (Gate K/L/M artefact exports that tests read use it explicitly). Default output when `--out` is omitted: `<RESEARCH_HOME>/exports/<project>/<task-or-scope>/<closure-digest-12>/`, e.g. `sample/gate L/_eval_home/exports/GATEL_REAL/…` — outside every store and ignored by git |
| timestamp vs reproducibility | two files: `manifest.json` (deterministic: exporter version, `closure_digest`, `corpus_snapshot_id`, files with sha256, provenance class counts, `NOT_AVAILABLE` list) and `export_info.json` (variable: exported_at, host, tool versions, global `state_sha256` for audit). Reproducibility = byte-equality of all content files and `manifest.json`; `export_info.json` is excluded and is the only variable file. Content files contain no timestamps other than those of the objects themselves |
| global `state_sha256` changes with unrelated writes | `closure_digest` = sha256 of the sorted `(object_id, revision, revision_digest)` of the task's dependency closure (objects reachable from the task through edges, decisions bound to them, run rows with their `artifact_sha256`) plus `corpus_snapshot_id`. Unrelated project writes do not change it; a new decision on the task does |
| floats, ordering | fixed formatting (repr of rounded values with declared digits), ids sorted, Unicode NFC, `\n` line ends |

---

## A10. Acceptance fixtures with expected outputs (synthetic; used by WP11 and by the truth-table tests)

Each fixture: corpus documents (short synthetic papers with sections), the candidate (scripted generator output), scripted adversarial proposals,
scripted researcher confirmations, and the expected pair verdict and candidate status.

| Fixture | Candidate X | Competitor P | Facets (confirmed) | Expected verdict → status | Error it guards against |
|---|---|---|---|---|---|
| F1 | CLAIM: mechanism M2 for fraud persistence | same population and period, mechanism M1 | `REL` | RELATED → NOT_CHALLENGED (if others clear) | false pre-emption (review example 1) |
| F2 | CLAIM: mechanism M, US firms | mechanism M, EU firms; X gives no scope reason | `E`, `DIS`, `NR` | PREEMPTS → CHALLENGED | missed pre-emption (review example 2) |
| F3 | as F2 with a confirmed theoretical reason (enforcement regime) | as F2 | `E`, `DIS`, `R` | PARTIALLY_PREEMPTS (left: boundary test; suggest SCOPE) → PARTIALLY_ADDRESSED | over-flagging when a reason exists |
| F4 | SCOPE: claim K fails in small firms | P tested K in all firms incl. small | `E`, `COV` | PREEMPTS → CHALLENGED | — |
| F5 | SCOPE: K fails in small firms | P tested K in large firms only | `E`, `DIS` | BUILDS_ON → NOT_CHALLENGED | treating the base finding as pre-emption |
| F6 | CLAIM: K | P finds not-K | `OPP` | CONTESTS → NOT_CHALLENGED + `contested_by: P` | treating disagreement as pre-emption |
| F7 | CLAIM: K | P claims K, but its test set overlaps training periods (confirmed inference NOT_SUPPORTED) | `E`, FLAWED | PARTIALLY_PREEMPTS (left: credible test) | trusting a flawed competitor (A5) |
| F8 | EVIDENCE: causal design for K | P correlational evidence of K | `E`, `NEW_TYPE` | BUILDS_ON → NOT_CHALLENGED | — |
| F9 | EVIDENCE: causal design for K | P used the same causal design | `E`, `SAME_TYPE`, `NR` | PREEMPTS (row 23) → CHALLENGED | — |
| F10 | METHOD: measure M for K | P used M for K | `E`, `SAME`, `NR` | PREEMPTS (row 18) | — |
| F11 | CLAIM: K | P's text lacks a method section | `U` | UNDETERMINED → INSUFFICIENT_COVERAGE | `NOT_CHALLENGED` on incomplete text |
| F12 | CLAIM about "financial reporting fraud" | P uses "earnings manipulation" and "AAER" only | `E` | with R2: found → CHALLENGED; with R2 disabled: not retrieved → NOT_CHALLENGED@S (test asserts the coverage block lists R2 as disabled) | terminology drift |
| F13 | CLAIM: K | P establishes K; scripted model proposes `NA` (wrong) | proposal `NA`, researcher corrects to `E` | before correction: NOT_ASSESSED (unconfirmed); after: scope facets become sensitive (A2.2) and are queued; final PREEMPTS | silent false negative from the model |
| F14 | held-out (A6.2) | P\* withheld then imported, BM25 rank > k | — | S1: NOT_CHALLENGED@S1 → STALE_FOR_CORPUS → CHALLENGED@S2 | corpus-dependent invalidation |
| F15 | several papers on one topic with two terminology families, one erratum revising a finding, two competing findings | — | — | landscape: one contradiction entry (suite inference), the erratum revises the finding's passage (old assessment NOT_ASSESSED) | realistic crowding (review P1) |
| F16 | cold start A2 / A3 / A4 | — | — | as the A3 table | contradiction between Test A and WP5 |

A **real-world adversarial corpus** for Gate M real (decision D23) should contain, from papers the owner may use and send to the provider: ≥15
full texts on fraud/misstatement detection; at least two terminology families (fraud / misstatement / earnings manipulation / AAER-based labels);
competing findings (e.g. machine-learning vs ratio-based models); the Bao 2020 erratum (`SRC_BAO2022ERRATUM`, already held) as a revision case; one
held-out competitor chosen by the owner.

---

## A11. Effect on work packages, effort and decisions

| WP | Change |
|---|---|
| WP0 | unchanged |
| WP1 | + A7 (UoW commit rule, `produced_by_run`, orphan detection, fixing `run_protocol` and `eca.adjudicate`), + task lease field; +1.5 days (expected 3.5) |
| WP2 | + A9 (path rule, two manifests, closure digest); +0.5 day (expected 4) |
| WP3 | + A8 (reservation, lease); +1 day (expected 6) |
| WP5 | + A3 floor, A4 snapshots and delta screening; +2 days (expected 7, R3b +2 separate) |
| WP6 | `evidence_permits` replaced by A5 dimensions; method assessment record; +1.5 days (expected 5.5) |
| WP7 | candidate fields of A1.1; +0.5 day (expected 3.5) |
| WP8 | rewritten around A1–A2: `CompetitorAssessment`, truth table, sensitivity loop, negative-facet judgement sheet; +2.5 days (expected 8) |
| WP9 | status lattice, optional rows; +0.5 day (expected 4.5) |
| WP11 | fixtures F1–F16, tests K, L, N; +2 days (expected 5) |
| WP12 | five components, baseline B0, blind rating, held-out protocol; dev +1 day (expected 3); researcher time +4–8 h |
| New WP14 | truth-table specification as data + completeness test, shared by WP8 and WP11 (written first, reviewed by the owner); 1 / 1.5 / 2 days |

Revised totals: **development ≈ 64 days expected (best ≈ 47, worst ≈ 92); researcher ≈ 20–30 h** (more blind reference work: interpretations,
competitors, pair verdicts, candidate ratings, decisions).

Sequencing change: WP14 (truth table as data, reviewed by the owner) comes **before** WP7/WP8 and is the checkpoint for approving WP3–WP13.

Decisions:

| | Status after this amendment |
|---|---|
| D16–D22 | recommendations unchanged; D20 and D21 confirmed as not blocking Contribution Discovery |
| **D23 (new)** | composition of the real Gate M corpus: pilot only (A4, `--accept-small-corpus`, tests pre-emption detection only) vs an adversarial corpus of ≥15 usable full texts (enables NOT_CHALLENGED and MR/MA metrics). Recommended: the adversarial corpus; owner selects papers and records rights |
| **D24 (new)** | thresholds for Gate M components (MF, MG, MR, MA, MU) and `min_fulltext_sources`, declared before the run. Recommended defaults in A6 and A3 |
| **D25 (new)** | baseline for candidate quality: B0 only, or B0 + researcher's own candidates (B1). Recommended B0 + B1 if the owner has time; B1 also gives the owner a record of their prior ideas |

## A12. Traceability: superseded passages

| Document | Section | Superseded by |
|---|---|---|
| `M5_CONTRIBUTION_DISCOVERY_PROTOCOL.md` | §5.2 `evidence_permits` layer | A5 |
| same | §5.4 required fields | A1.1 (fields added; others kept) |
| same | §5.5 steps 3, ten-dimension outcomes as verdict inputs, status table, extra-status evaluation | A1.2–A1.4, A2 |
| `M5_KNOWLEDGE_AND_INTEGRATION.md` | §9.2 rows "new paper imported", "re-retrieval returns a new competitor" | A4 |
| `M5_GATE_M_AND_BENCHMARKS.md` | §2 metrics, §3 tests A–J and pass rule, §4 real pilot, §6 ECA tiering example | A6, A3, A2.3, A10 |
| `M5_TASKS_AND_DATA_MODEL.md` | §4 resume/idempotency, §7 budget | A7, A8 |
| `M5_EXPORT_CONTRACT.md` | §1 safe paths, reproducibility, manifest | A9 |
| `M5_WORK_PACKAGES.md` | efforts, WP8 scope, totals | A11 |
| `M5_AUDIT.md` | CI numbers | R11 (corrected in place) |
| `PLAN_M5_TASKS_AND_DISCOVERY.md` | §1 effort, §20 decisions, §22 roadmap, §23 milestones | A11 (updated in place) |
