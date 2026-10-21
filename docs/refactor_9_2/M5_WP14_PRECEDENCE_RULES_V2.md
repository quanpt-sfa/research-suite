# WP14 v2 — precedence rules, source status and versions (for direct audit)

Status: **design for audit. Not a production rule. Not implemented.** Replaces `M5_WP14_TRUTH_TABLE_DRAFT.md` (v1, kept for history). Machine-readable
fixtures: `wp14/fixtures_v2.json`. Blank form for the independent expert cases: `wp14/expert_cases_form.json` (to be filled by the researcher, never
by a model).

**Principle P0.** *Scientific priority, evidentiary validity and publication status are distinct dimensions. None automatically determines the others.*

## 1. Corrections against v1 (third review, 2026-10-22)

| Review finding | v1 defect | v2 rule |
|---|---|---|
| A. CORRECTION blocked by P2 | `claim_rel = NA` returned UNRELATED before `critique_rel` was read; CORRECTION mixed two propositions | CORRECTION split into **`FLAW_IDENTIFICATION`** and **`CORRECTED_RESULT`**; each kind has its own *primary facet*; generic rows act on the primary facet; facets that do not apply to a kind must be `N/A` (consistency check §3.3) |
| B. `PART` inconsistent (P11, P15, P18 could give PRECEDES) | `SAME/PART` grouped together | rule **R-PART**: anything less than a full match on any required component yields at most PARTIALLY_PRECEDES, with the missing component named (§4) |
| C. Status order hid corpus change | one enum mixed progress, currency and outcome | three separate fields: `assessment_progress`, `corpus_currency`, `precedence_outcome` (§6) |
| D. No rule for several competitors jointly covering a claim | union of "remaining" | **joint coverage** over scope cells, only with consistent conclusions, never across components that must hold within one paper (§5) |
| E. F12 hid a retrieval miss; MA3 measured after human correction | — | coverage reports the assessed set; Gate M counts a missed gold competitor as a retrieval error; MA3a (before correction) and MA3b (after) (§8) |
| Fixtures not machine-checkable; PARTIALLY rows without counterexamples; expert cases not yet written | — | structured fixtures with all fields; counterexamples for every PRECEDES and PARTIALLY row (§7); expert cases remain a **deliverable of the researcher**, form provided |
| Working papers | not handled | source status layer, versions as one work, editorial relevance (§9) |

## 2. Claim kinds

| Kind | Proposition | Primary facet | Threshold disclosure for full precedence |
|---|---|---|---|
| `MECHANISM` | mechanism X explains Z (optionally *rather than* rival M1: `rival` field) | `claim_rel` | `PROPOSED` (priority of proposing; not a test) — with a `rival`, `TESTED` (a discriminating test) |
| `FINDING` | relation K holds in scope S | `claim_rel` + scope | `TESTED` |
| `BOUNDARY` | K holds / fails under condition Y (rationale required) | `claim_rel` on K + condition | `TESTED` |
| `METHOD` | measurement / identification M answers question Q | `question_rel` + `method_rel` | `APPLIED` |
| `EVIDENCE` | evidence of type T for K | `claim_rel` + `evidence_rel` | `TESTED` |
| `FLAW_IDENTIFICATION` | study W has flaw F | `critique_rel` | `PROPOSED` (the flaw stated with an argument) |
| `CORRECTED_RESULT` | with F corrected, K′ holds | `claim_rel` on K′ + `correction_rel` | `TESTED` |

Representational check requested by the review: *distinguishing rival mechanisms* is a `MECHANISM` claim with `rival`; *measurement validation* is
`METHOD` with Q = "does M measure construct C"; *theory test* is `FINDING` or `EVIDENCE` on the theory's prediction. New kinds are added only if
expert cases show a case the seven kinds cannot express (§10).

## 3. Facets

### 3.1 Values

| Facet | Values |
|---|---|
| `claim_rel` / `question_rel` | `SAME` same proposition / question; `SUBSET` a narrower or weaker form (part of the proposition); `OPP` same question, contrary answer; `REL` related, different proposition; `NA` not addressed in the inspected text (needs an inspection record); `U` undecidable from the text held; `N/A` not applicable to this kind |
| `disclosure` | `MENTIONED_VAGUE`, `MENTIONED_SPECIFIC` (named precisely, e.g. as future research), `PROPOSED` (stated as the paper's hypothesis or argument), `DEVELOPED` (argued in full, predictions derived), `TESTED` / `APPLIED` (confronted with data / method used) |
| `scope_cover` (FINDING, also for joint coverage) | structured scope of P (§5.1) → `COV`, `OVL`, `DIS`, `NS` relative to the claim's scope |
| `condition_rel` (BOUNDARY) | `TESTED`, `TESTED_PART`, `POOLED`, `NOT_TESTED`, `NS` |
| `method_rel` (METHOD) | `SAME`, `SIMILAR` (variant measuring a different construct or using different identifying variation), `DIFF` |
| `evidence_rel` (EVIDENCE) | `SAME_TYPE` (design *as described* is of type T), `OTHER_TYPE`, `NO_EVIDENCE` (P presents no evidence for K) |
| `critique_rel` (FLAW_IDENTIFICATION) | `SAME` same flaw in the same study; `PART` the same flaw stated less specifically, or a symptom of it; `OTHER`; `NONE`; `U` |
| `correction_rel` (CORRECTED_RESULT) | `SAME_CORRECTION` P re-estimated with F corrected; `OTHER_CORRECTION`; `NONE` |

### 3.2 Applicability (facets that are not listed for a kind must be `N/A`)

| Kind | Required facets |
|---|---|
| MECHANISM | `claim_rel`, `disclosure` |
| FINDING | `claim_rel`, `disclosure`, `scope_cover` |
| BOUNDARY | `claim_rel`, `disclosure`, `condition_rel` |
| METHOD | `question_rel`, `disclosure`, `method_rel` |
| EVIDENCE | `claim_rel`, `disclosure`, `evidence_rel` |
| FLAW_IDENTIFICATION | `critique_rel`, `disclosure` |
| CORRECTED_RESULT | `claim_rel` (on K′), `disclosure`, `correction_rel` |

### 3.3 Consistency rules (refused at storage, `FACET_INCONSISTENT`)

* A facet not required by the kind is `N/A`; a required facet is never `N/A`.
* If the primary facet is `NA`, `REL` or `U`, the kind-specific facets are `N/A` and `disclosure` is `N/A`.
* `disclosure = TESTED/APPLIED` requires a passage from P's results or methods section (locator checked); `PROPOSED/DEVELOPED` requires a passage stating
  the proposition; `MENTIONED_*` requires the mentioning passage.
* `critique_rel = SAME/PART` requires P to name the study W (citation resolved to a source id or bibliographic identity).
* Negative values (`NA`, `NOT_TESTED`, `NONE`) require an inspection record (v1 §5, unchanged): if material that could address the claim is known to
  be missing, the value is `U`.

## 4. Pair precedence

Evaluation is in two steps: the **generic step** on the primary facet, then the **component step**.

**Generic step** (on the primary facet of the kind):

| Row | Primary facet | Result |
|---|---|---|
| G1 | `U` | UNDETERMINED |
| G2 | `NA` | UNRELATED |
| G3 | `REL` | RELATED |
| G4 | `OPP` | CONTESTS |
| G5 | `SAME` or `SUBSET` | go to the component step |

For `FLAW_IDENTIFICATION` the primary facet is `critique_rel`: `U`→G1, `NONE`→G2, `OTHER`→G3, `SAME`/`PART`→component step (`PART` counts as `SUBSET`).
This removes the v1 defect: a paper that identified the flaw is assessed on `critique_rel`, whatever `claim_rel` says about the corrected result,
which is a separate claim (`CORRECTED_RESULT`).

**Component step.** Each kind requires components; each component is `FULL`, `PARTIAL`, `NONE` or `UNKNOWN` for the pair:

| Component | FULL | PARTIAL | NONE | UNKNOWN |
|---|---|---|---|---|
| `proposition` | primary = `SAME` | primary = `SUBSET` | — | — |
| `disclosure` | ≥ threshold of the kind (§2) | `MENTIONED_SPECIFIC` … one level below threshold or more, but ≥ `MENTIONED_SPECIFIC` | `MENTIONED_VAGUE` | — |
| `scope` (FINDING) | `COV` | `OVL` | `DIS` | `NS` |
| `condition` (BOUNDARY) | `TESTED` | `TESTED_PART` | `POOLED`, `NOT_TESTED` | `NS` |
| `method` (METHOD) | `SAME` | `SIMILAR` | `DIFF` | — |
| `evidence` (EVIDENCE) | `SAME_TYPE` | — | `OTHER_TYPE`, `NO_EVIDENCE` | — |
| `correction` (CORRECTED_RESULT) | `SAME_CORRECTION` | `OTHER_CORRECTION` | `NONE` | — |

Rule in order:

```text
if any component is UNKNOWN:                         UNDETERMINED (name the unknown component)
if disclosure == NONE:                                RELATED (vague mention only)
if a kind-specific component (scope/condition/method/evidence/correction) == NONE:
                                                      BUILDS_ON (P is the base; the claim's distinctive component is untouched)
if every component == FULL:                           PRECEDES
otherwise (R-PART):                                   PARTIALLY_PRECEDES, remaining = [components that are PARTIAL, with what is missing]
```

R-PART is the only route to PARTIALLY_PRECEDES and no route with a PARTIAL component reaches PRECEDES — this fixes review finding B for every kind at
once instead of row by row. `remaining` is structured: `{component, missing}` such as `{scope: cells not covered}`, `{disclosure: "tested" needed,
P only proposed}`, `{method: "construct C vs P's construct"}`.

Credibility (Amendment 1 §A5 dimensions) is never read here (invariant I-1). Publication status is never read here (invariant I-10).

## 5. Joint coverage across competitors

### 5.1 Structured scope

A FINDING claim and each competitor carry a scope as a product of dimensions: `period` (closed year intervals), `population` and `market` (sets of
controlled tags from the domain spec, e.g. `US_listed`, `VN_listed`, `SOE`), `unit` (tag). A dimension a paper does not state is `NS`.

### 5.2 Which pairs may combine

Only components that describe **where** a claim was established combine across papers (scope cells for FINDING, condition cells for BOUNDARY).
Components that must hold **within one paper** never combine: the proposition (`SAME`), the disclosure level (`TESTED`), the method, the evidence type,
the correction. Two vague mentions do not make a proposal; two partial methods do not make the method.

A pair is **eligible** for joint coverage if its proposition = FULL, disclosure = FULL and (for METHOD/EVIDENCE/CORRECTED_RESULT) its kind-specific
component = FULL, and it is confirmed. Versions of one work count once (§9.3).

### 5.3 Algorithm (deterministic)

```text
def joint_coverage(claim, pairs):
    cells = partition(claim.scope, breakpoints from all eligible and contesting pairs)    # elementary cells: interval pieces x tags
    for c in cells:
        sup  = [p for p in eligible(pairs)  if covers(p.scope, c)]
        opp  = [p for p in confirmed(pairs) if p.primary == OPP and covers(p.scope, c)]
        unk  = [p for p in confirmed(pairs) if p.scope has NS on a dimension that could include c]
        c.state = (CONTESTED if sup and opp else          # supported and contradicted in the same cell
                   COVERED   if sup else
                   CONTRARY  if opp else                   # only contrary evidence: not covered, flagged
                   UNKNOWN   if unk else
                   UNCOVERED)
    if all cells COVERED:                      return JOINTLY_PRECEDED(basis = minimal set of works covering all cells)
    if any cell COVERED or CONTESTED:          return JOINTLY_PARTIAL(remaining = UNCOVERED + UNKNOWN cells,
                                                                      contested = CONTESTED cells, contrary = CONTRARY cells)
    return NO_JOINT_COVERAGE                   # CONTRARY cells still raise the CONTESTED flag on the claim
```

* Example (review D): claim K for 2000–2020; P1 tested 2000–2010, P2 tested 2011–2020, both `SAME` → all cells COVERED → JOINTLY_PRECEDED (basis P1+P2).
* If P2 found not-K for 2011–2020: cells 2011–2020 CONTRARY → JOINTLY_PARTIAL (2000–2010 covered), flag CONTESTED; residual opportunity "explain
  the change after 2010" (suggested BOUNDARY claim). If a third paper P3 also found K for 2011–2020, those cells would be CONTESTED.
* If P2's population is `NS`: cells of 2011–2020 UNKNOWN → JOINTLY_PARTIAL with remaining "population of P2 unknown".
* `partition` is exact (finite cells) because dimensions are intervals and finite tag sets; the domain spec defines the tag vocabulary; a free-text scope
  makes the pair ineligible (stated in `remaining`).

The claim's precedence outcome uses joint coverage (§6): JOINTLY_PRECEDED counts as CHALLENGED with basis "joint", and the export lists the covering works.

## 6. Claim state: three fields, not one enum

| Field | Values | Determined by |
|---|---|---|
| `assessment_progress` | `NONE`, `PARTIAL (k of n pairs confirmed)`, `COMPLETE` | confirmations of the pairs bound to the current snapshot and claim revision |
| `corpus_currency` | `CURRENT@S`, `STALE_FOR_CORPUS(S_old → S_new, screened k of m)` | snapshot of the latest completed screening vs the project's current snapshot (I20) |
| `precedence_outcome` | `CHALLENGED`, `PARTIALLY_ADDRESSED`, `NOT_CHALLENGED_IN_SEARCHED_CORPUS`, `INSUFFICIENT_COVERAGE`, `NOT_YET_DETERMINED` | rule below |

```text
def precedence_outcome(claim):
    conf = confirmed pairs (current claim revision; current or older snapshots — confirmed facts do not expire, I-2)
    if any(p.precedence == PRECEDES for p in conf) or joint_coverage(claim, conf) == JOINTLY_PRECEDED:
        return CHALLENGED                                    # positive evidence: valid whatever the currency
    if any(p.precedence == PARTIALLY_PRECEDES for p in conf) or joint == JOINTLY_PARTIAL:
        return PARTIALLY_ADDRESSED                           # "at least partially"; may still become CHALLENGED
    # from here on the outcome rests on absence: it needs a complete assessment on the current corpus
    if claim.assessment_progress != COMPLETE or claim.corpus_currency is STALE_FOR_CORPUS:
        return NOT_YET_DETERMINED
    if coverage.below_floor or any(p.precedence == UNDETERMINED for p in conf) or joint has UNKNOWN cells:
        return INSUFFICIENT_COVERAGE
    return NOT_CHALLENGED_IN_SEARCHED_CORPUS
```

Display: the three fields are always shown together, e.g. `PARTIALLY_ADDRESSED · progress 3/5 · STALE_FOR_CORPUS (+12 sources, 4 screened)`. The
original specification's `NOT_ASSESSED` is shown as `NOT_YET_DETERMINED · progress NONE`.

Candidate summary (unchanged in meaning, computed from outcomes): `ALL_CLAIMS_CHALLENGED`, `MIXED`, `INSUFFICIENT_COVERAGE`,
`NO_CLAIM_CHALLENGED_IN_SEARCHED_CORPUS`, `UNDETERMINED_CLAIMS_PRESENT` (any `NOT_YET_DETERMINED`).

## 7. Counterexamples for every PRECEDES and PARTIALLY path

| Path | Positive (expected result) | Counterexample (must not give the positive result) | Expected for the counterexample |
|---|---|---|---|
| MECHANISM PRECEDES | P states X as its hypothesis (`SAME`, `PROPOSED`) | P names X once as future research (`SAME`, `MENTIONED_SPECIFIC`) | PARTIALLY_PRECEDES, remaining disclosure |
| MECHANISM PARTIAL (disclosure) | as the counterexample above | P says "other mechanisms may play a role" (`MENTIONED_VAGUE`) | RELATED |
| MECHANISM PARTIAL (subset) | P proposes X for one type of fraud only (`SUBSET`, `PROPOSED`) | P proposes a different mechanism with the same variables (`REL`) | RELATED |
| MECHANISM with rival PRECEDES | P runs a test discriminating X from M1 (`SAME`, `TESTED`) | P proposes X and M1 side by side without a discriminating test (`SAME`, `DEVELOPED`) | PARTIALLY_PRECEDES, remaining "discriminating test" |
| FINDING PRECEDES | P tested K, scope covers S | P tested K on an overlapping period (`OVL`) | PARTIALLY_PRECEDES, remaining cells |
| FINDING PARTIAL (scope) | P tested K on 2000–2010, claim 2000–2020 | P tested K in a disjoint market (`DIS`) | BUILDS_ON (+ `NEW_SETTING_WITHOUT_RATIONALE` if no boundary claim) |
| FINDING PARTIAL (disclosure) | P hypothesised K for S without testing (`SAME`, `PROPOSED`, `COV`) | P tested K for S (`TESTED`) | PRECEDES |
| BOUNDARY PRECEDES | P reports K separately under Y (`TESTED`) | P's sample contains Y-cases, pooled (`POOLED`) | BUILDS_ON |
| BOUNDARY PARTIAL | P reports K under a related condition (`TESTED_PART`) | P reports K under an unrelated condition (`NOT_TESTED`) | BUILDS_ON |
| METHOD PRECEDES | P applied M to Q (`SAME`, `APPLIED`) | P applied a tone dictionary to Q (`SIMILAR`) | PARTIALLY_PRECEDES, remaining construct |
| METHOD PARTIAL | `SIMILAR` as above | P answered Q with an unrelated method (`DIFF`) | BUILDS_ON |
| EVIDENCE PRECEDES | P used the same staggered design for K (`SAME_TYPE`, `TESTED`) | P calls a cross-sectional regression "causal" (`OTHER_TYPE`) | BUILDS_ON |
| EVIDENCE PARTIAL | P used the same design for a narrower K (`SUBSET`) | P used it for a related K (`REL`) | RELATED |
| FLAW_IDENTIFICATION PRECEDES | P shows look-ahead features in W (`SAME`, `PROPOSED`) | P criticises W's sample size (`OTHER`) | RELATED |
| FLAW_IDENTIFICATION PARTIAL | P suspects "timing problems" in W's features (`PART`) | P cites W approvingly (`NONE` with inspection record) | UNRELATED |
| CORRECTED_RESULT PRECEDES | P re-estimates W without look-ahead (`SAME_CORRECTION`, `TESTED`) | P identified the flaw but did not re-estimate (`claim_rel NA`) | UNRELATED for this claim (the flaw claim is assessed separately) |
| CORRECTED_RESULT PARTIAL | P re-estimates with a different fix (`OTHER_CORRECTION`) | P re-estimates W for other reasons (`correction_rel NONE`) | BUILDS_ON |
| Joint coverage | P1 2000–2010 + P2 2011–2020, both `SAME` | P2 finds not-K for 2011–2020 | JOINTLY_PARTIAL, contrary cells 2011–2020, flag CONTESTED |
| Same results, different explanations | candidate MECHANISM X2; P has the same variables and similar estimates, explained by X1 | — | MECHANISM: RELATED; a FINDING claim on the same relation: PRECEDES. The same numbers never make the contributions the same |

## 8. Coverage of what was actually read; Gate M measurement

* Every absence-based outcome carries: corpus snapshot, **assessed set** (sources actually retrieved and assessed, with counts against the corpus
  size), retrieval configuration (R1/R2/R4 on or off, k), inspection-record summary. "Searched corpus" in `NOT_CHALLENGED_IN_SEARCHED_CORPUS` means the
  assessed set, and the export says so in the same line.
* Gate M **MR**: a gold competitor present in the corpus but absent from the assessed set is a retrieval miss, whatever the claim outcome; fixture F12
  with R2 disabled is scored as a miss.
* Gate M **MA** layers: MA1 facet coding (model vs reference coding); MA2 rule aggregation (rules applied to the reference coding vs the expert's
  expected outcome); **MA3a** end to end *before* any human correction (model facets → rules → outcome, vs reference); **MA3b** after correction (final
  human-AI outcome). Both MA3a and MA3b are reported; neither replaces the other.

## 9. Working papers, preprints and versions

### 9.1 Source status (a layer independent of the precedence rules)

| Field | Values / content | Recorded from |
|---|---|---|
| `document_type` | `journal_article`, `working_paper`, `preprint`, `conference_paper`, `dissertation`, `report`, `book_chapter`, `other` | researcher, metadata |
| `review_status` | `NOT_REVIEWED`, `UNDER_REVIEW`, `ACCEPTED`, `PUBLISHED`, `UNKNOWN` | metadata, the document itself |
| `first_public_date` | date + `date_evidence` (repository posting page, DOI registration, arXiv/SSRN record), or `UNKNOWN` | never inferred from the PDF's own text alone |
| `version_id` | repository version label or posting date | metadata |
| `work_id` | identity of the work across versions | version relations below |
| `version_relations` | `is_version_of`, `has_version`, `is_preprint_of` (Crossref relation types), `same_work_asserted_by_researcher` | Crossref metadata (D17 opt-in) or the researcher |
| `relation_to_researcher` | `INDEPENDENT`, `OWN_PRIOR_WORK` | researcher (own earlier working papers are shown separately, not as competitors) |

Stored on `Source` (schema extension, additive) and as `derived_from` edges with `edge_kind: version_of` between versions (no new relation; edges SQL
CHECK unchanged).

### 9.2 Three output dimensions per pair

| Dimension | Values | From |
|---|---|---|
| `intellectual_precedence` | the pair precedence of §4 | facets, never the source status |
| `evidentiary_credibility` | `reading`, `inference`, `reproduction` (Amendment 1 §A5) | method assessment; review status is shown next to it but does not set it |
| `editorial_relevance` | `COUNTS_AS_PRIOR_WORK`, `EXCLUDED_BY_POLICY`, `POLICY_DEPENDENT`, `NO_POLICY_RECORDED` | the target journal's preprint / working-paper policy from the Journal Policy Registry (`knowledge/journal_policy.py::lookup`), which requires an official excerpt with a snapshot; only applies to `NOT_REVIEWED` / `UNDER_REVIEW` documents |

Example: a working paper may be `PRECEDES · NOT_REVIEWED · inference NOT_ASSESSED · POLICY_DEPENDENT`; nothing in that line is contradictory.
`WORKING_PAPER_PREEMPTS` is **not** a status (it would mix document type with the knowledge relation).

### 9.3 Versions are one work

* Each version is a Source assessed on its own text; pairs are per version.
* **Work-level precedence date** for a claim: the `first_public_date` of the earliest version whose pair reaches the given precedence (e.g. V1 proposed X →
  priority of proposal 2024 even if the journal version dropped it).
* **Work-level current content**: the latest version. If a later version withdraws or changes a claim, the earlier precedence remains (priority of
  public disclosure is not undone, I-12), the work's current finding changes, the earlier version's credibility carries the annotation
  `withdrawn_in_later_version` (scope of the withdrawal), a residual opportunity "credible test" is suggested, and the export shows both with dates.
* Joint coverage and any count of "independent" studies count a work once.
* A new version entering the corpus changes the snapshot (I20): pairs of that work are re-screened; earlier pairs are kept as history.

### 9.4 Invariants added

| Id | Invariant |
|---|---|
| I-10 | Publication or review status never changes `intellectual_precedence` or `evidentiary_credibility` |
| I-11 | Versions of one work count once in joint coverage and in counts of studies |
| I-12 | A later version never deletes an earlier version's precedence; it can change the work's current content |
| I-13 | `editorial_relevance` is `NO_POLICY_RECORDED` unless a registry policy with an official excerpt exists for the target journal |

(I-1 … I-9 from v1 are kept; I-1 now reads "credibility and publication status never change precedence".)

## 10. Decisions on the four questions (reviewer's recommendation; working decisions pending the owner's confirmation)

| Question | Working decision |
|---|---|
| Does proposing a mechanism count as prior? | Yes, for **priority of proposal** (`MECHANISM` threshold `PROPOSED`); it does not mean the mechanism is tested or established |
| A future-research remark? | `MENTIONED_SPECIFIC` → PARTIALLY_PRECEDES (remaining: development / test); a vague remark → RELATED; never treated as developed or tested |
| An adjacent market? | Decided by the claim kind and the structured scope; adjacency itself decides nothing |
| More claim kinds? | First test whether the seven kinds express the expert cases (rival mechanisms, measurement validation, theory test are expressed as in §2); add kinds only for cases they cannot express |

## 11. Acceptance of WP14 (unchanged gate, updated contents)

1. Rules of §3–§6 as versioned data; totality and consistency tests (every admissible facet combination per kind gives exactly one result; inconsistent
   combinations are refused).
2. `wp14/fixtures_v2.json` passes completely (structured; no free-text expectations).
3. Independent expert cases: **≥ 20, written by the researcher** on `wp14/expert_cases_form.json`, covering at least: rival explanations with the same
   results; a working paper fully developing a mechanism; a future-research mention followed by a full test; a multi-version work whose journal version
   withdraws a result; joint coverage by two papers; a correction already published; pooled subsamples. Not derived from the fixtures. Disagreements
   between rules and cases resolved and recorded.
4. Invariants I-1 … I-13 as property tests.
5. Owner approval of the rules version. Until then the rules are not used to produce any status shown as an assessment.
