# WP14 draft — scientific truth table for prior-art precedence (for direct review)

Status: **draft for the owner's and reviewer's audit. Not a production rule. Not implemented.** It replaces the 25-row table of
`M5_DESIGN_AMENDMENT_1.md` §A1.3 (kept there for history). Changes follow the second design review (Amendment 2, `M5_DESIGN_AMENDMENT_2.md`).

This document is self-contained so that it can be audited without the other M5 documents.

## 1. What changed against Amendment 1 and why

| Amendment 1 | Problem raised in review | This draft |
|---|---|---|
| one verdict per (candidate, competitor) | a candidate holds several contributions with different fates; one label loses the part still worth pursuing | verdicts per **contribution claim**; the candidate is summarised from its claims |
| row 7: same mechanism, other market, no reason → whole candidate CHALLENGED | the boundary contribution (institution Y as a test of universality) is a different claim and must be judged on its own | the *mechanism-proposal* claim is PRECEDED; a *boundary* claim, if the candidate states one, is assessed separately; a plain *finding in a new setting* is not preceded, but flagged for significance |
| `p_quality = FLAWED` changes the verdict (rows 5, 17, 22) | a weak earlier paper still proposed or tested the idea first; quality is about credibility and leftover opportunity, not precedence | three separate outputs: **precedence**, **credibility**, **residual opportunity**; credibility never changes precedence |
| table completeness = correctness | the model can code facets wrongly; a complete table then gives a valid-looking wrong answer | Gate M measures facet coding and table aggregation separately (§7); expert cases test the table itself (§6) |

## 2. Units

* **Contribution claim** (`ContributionClaim`): one proposition a study would add. A candidate has one or more. Kinds:

| Kind | Form | Example |
|---|---|---|
| `MECHANISM` | "mechanism X explains phenomenon Z" (a proposed explanation) | "Managers shift from accrual to real manipulation when detection models weight accruals" |
| `FINDING` | "relation K holds in scope S" (an empirical claim with its scope) | "K holds among Vietnamese listed firms 2010–2022" |
| `BOUNDARY` | "K holds / fails under condition Y" with a stated rationale why Y matters | "K fails where state ownership weakens market discipline" |
| `METHOD` | "measurement or identification M answers question Q" | "a text-based measure of pressure identifies K" |
| `EVIDENCE` | "evidence of type T for K" (e.g. the first quasi-experimental test) | "staggered adoption gives a quasi-experimental test of K" |
| `CORRECTION` | "earlier conclusion on K does not follow because of flaw F; a corrected test shows …" | "P's out-of-sample result uses look-ahead features" |

* Decomposition of a candidate into claims is proposed by the generator, shown to the researcher, and editable (split / merge / narrow are decision
  actions). Its correctness is measured (MA1, §7).
* **Pair** = (claim, competitor P at a source revision, corpus snapshot).

## 3. Facets coded per pair (by the model, checked by ECA or the researcher)

| Facet | Values | Coded from |
|---|---|---|
| `claim_rel` | `SAME` P states the same proposition as its own; `PART` a weaker or narrower form, or only as a conjecture / future-research remark; `OPP` same question, contrary answer; `REL` related, different proposition; `NA` not addressed in the inspected text; `U` cannot be decided from the text held | P's passages (positive values); inspection record (negative values, §5) |
| `scope_rel` (FINDING) | `COV` P's tested scope covers S; `OVL` overlaps; `DIS` disjoint; `NS` not stated | P's sample description |
| `condition_rel` (BOUNDARY) | `TESTED` P reports K separately under Y (or a condition that includes Y); `TESTED_PART` under a related/overlapping condition; `POOLED` Y-cases are inside P's sample but not reported separately; `NOT_TESTED`; `NS` | P's results / subsample analyses |
| `method_rel` (METHOD) | `SAME` P used M for Q; `SIMILAR` a variant that measures a different construct or uses a different identifying variation; `DIFF` | P's methods section, design as described (not P's labels) |
| `evidence_rel` (EVIDENCE) | `SAME_TYPE` P's design, as described, is of type T for K; `OTHER_TYPE` | P's methods section, design as described; P calling a correlation "causal" does not make it `SAME_TYPE` |
| `critique_rel` (CORRECTION) | `SAME_CRITIQUE` P (a competitor of the candidate) already identified flaw F in the target study; `OTHER_CRITIQUE`; `NONE` | P's passages |

Credibility of P (`reading`, `inference`, `reproduction`, Amendment 1 §A5) is recorded on the pair as an **annotation**; it is not an input of §4.

## 4. Precedence table (first matching row wins within the claim kind)

Outputs: `PRECEDES`, `PARTIALLY_PRECEDES` (with `remaining`), `CONTESTS`, `BUILDS_ON`, `RELATED`, `UNRELATED`, `UNDETERMINED`.

| Row | Kind | Condition | Precedence | `remaining` (what P leaves open for this claim) |
|---|---|---|---|---|
| P1 | any | `claim_rel = U` | UNDETERMINED | what was missing (§5) |
| P2 | any | `claim_rel = NA` | UNRELATED | — |
| P3 | any | `claim_rel = REL` | RELATED | — |
| P4 | any | `claim_rel = OPP` | CONTESTS | the disagreement itself (never a precedence) |
| P5 | MECHANISM | `SAME` | **PRECEDES** | — (scope differences do not matter for *proposing* a mechanism; see §8 row-7 note) |
| P6 | MECHANISM | `PART` | PARTIALLY_PRECEDES | first full development of X (P mentions it as conjecture, or a narrower version) |
| P7 | FINDING | `SAME`/`PART`, `scope_rel = COV` | **PRECEDES** (`PART` → PARTIALLY_PRECEDES) | — / the stronger form |
| P8 | FINDING | `SAME`/`PART`, `scope_rel = OVL` | PARTIALLY_PRECEDES | the non-overlapping part of S |
| P9 | FINDING | `SAME`/`PART`, `scope_rel = DIS` | BUILDS_ON | the whole claim (S not covered); significance flag (§4.2) |
| P10 | FINDING | `SAME`/`PART`, `scope_rel = NS` | UNDETERMINED | P's scope |
| P11 | BOUNDARY | `SAME`/`PART` on K, `condition_rel = TESTED` | **PRECEDES** | — |
| P12 | BOUNDARY | `SAME`/`PART`, `condition_rel = TESTED_PART` | PARTIALLY_PRECEDES | Y itself as opposed to the related condition |
| P13 | BOUNDARY | `SAME`/`PART`, `condition_rel ∈ {POOLED, NOT_TESTED}` | BUILDS_ON | the boundary test |
| P14 | BOUNDARY | `SAME`/`PART`, `condition_rel = NS` | UNDETERMINED | P's subsample reporting |
| P15 | METHOD | `SAME`/`PART` on Q, `method_rel = SAME` | **PRECEDES** | — (a new scope for the same method is a FINDING claim) |
| P16 | METHOD | `SAME`/`PART`, `method_rel = SIMILAR` | PARTIALLY_PRECEDES | what M measures or identifies that the variant does not |
| P17 | METHOD | `SAME`/`PART`, `method_rel = DIFF` | BUILDS_ON | — |
| P18 | EVIDENCE | `SAME`/`PART` on K, `evidence_rel = SAME_TYPE` | **PRECEDES** | — (credibility of that evidence: §4.1) |
| P19 | EVIDENCE | `SAME`/`PART`, `evidence_rel = OTHER_TYPE` | BUILDS_ON | — |
| P20 | CORRECTION | `critique_rel = SAME_CRITIQUE` | **PRECEDES** | — |
| P21 | CORRECTION | `critique_rel = OTHER_CRITIQUE` | RELATED | — |
| P22 | CORRECTION | `critique_rel = NONE` | UNRELATED | — |

For CORRECTION claims `claim_rel` is coded against the corrected proposition; rows P1–P4 still apply first.

### 4.1 Credibility and residual opportunity (never inputs of precedence)

| Situation | Precedence (unchanged) | Residual opportunity shown (a *suggestion*, added as a claim only by the researcher) |
|---|---|---|
| P5/P7/P11/P15/P18 with P's `inference` confirmed `LIMITED` or `NOT_SUPPORTED` | PRECEDES | "a credible test of the same claim" → suggested `EVIDENCE` or `CORRECTION` claim naming the flaw |
| P5 (mechanism proposed) and P tested it only in a scope disjoint from the candidate's | PRECEDES | "a boundary test under condition Y" → suggested `BOUNDARY` claim; it needs a rationale for Y |
| P4 CONTESTS | CONTESTS | "reconcile the contrary findings" → suggested claim of kind FINDING or BOUNDARY on the moderating condition |
| P9 BUILDS_ON without a boundary claim | BUILDS_ON | "state why S matters" → suggested `BOUNDARY` claim |

Invariant (§6.3 I-1): changing credibility annotations never changes any precedence value.

### 4.2 Significance flags (separate from precedence)

`NEW_SETTING_WITHOUT_RATIONALE` (a FINDING claim whose only competitor relations are BUILDS_ON via P9 and the candidate has no BOUNDARY claim),
`CONTESTED` (any CONTESTS), `CONJECTURE_ONLY_IN_PRIOR` (P6). Flags inform the researcher's decision; they never change a status.

## 5. Negative values need an inspection record

`claim_rel = NA` (and `condition_rel = NOT_TESTED`, `critique_rel = NONE`) is a claim about absence. It is recorded only with an **inspection record**:

| Field | Content |
|---|---|
| `text_held` | source revision, `text_sha256`, pages extracted / pages in the PDF, extraction warnings (tables as images, broken encoding) |
| `sections_inspected` | the stored sections (`corpus.source_sections`) that were searched and shown |
| `sections_missing` | expected but absent: references to an online appendix or supplementary material not held ("see Online Appendix"), tables not extracted, sections cut by the context budget |
| `search_terms` | the claim's terms, R2 expansions, synonyms used for the in-document search |
| `closest_passages` | the passages shown to the researcher, with scores |
| `scope_of_negative` | the sentence that is stored: "Not addressed in sections {…} of SRC@rev searched with terms {…}; appendix not held" |

Rule: if `sections_missing` contains material that could address the claim (an appendix, robustness tables), the value is `U`, not `NA`, unless the
researcher records that they checked the missing material (then they are the source of the judgement, `generated_by: researcher`).

## 6. WP14 products and their acceptance

### 6.1 Product 1 — the table with counterexamples for every PRECEDES row

| Row | Positive example (expected PRECEDES) | Counterexample (must **not** give PRECEDES) | Expected for the counterexample |
|---|---|---|---|
| P5 MECHANISM | P: "we argue managers substitute real for accrual manipulation when accrual-based detection rises" | P mentions substitution once in "future research" | `PART` → P6 PARTIALLY_PRECEDES |
| P7 FINDING | P tests K on all US listed firms 2000–2020; claim S = US manufacturing 2005–2015 | P tests K on US firms, the claim is about Vietnam | `DIS` → P9 BUILDS_ON + `NEW_SETTING_WITHOUT_RATIONALE` if no boundary claim |
| P11 BOUNDARY | P reports K separately for state-owned firms | P's sample includes state-owned firms but reports pooled results only | `POOLED` → P13 BUILDS_ON |
| P15 METHOD | P uses the same text-based pressure measure for K | P uses a dictionary measure of tone (a different construct) | `SIMILAR` → P16 PARTIALLY_PRECEDES |
| P18 EVIDENCE | P uses staggered adoption of the same regulation for K | P calls its cross-sectional regression "causal" | design is correlational → `OTHER_TYPE` → P19 BUILDS_ON |
| P20 CORRECTION | P already shows the look-ahead features in the target study | P criticises the target study's sample size only | `OTHER_CRITIQUE` → P21 RELATED |

Acceptance: every PRECEDES and PARTIALLY_PRECEDES row has at least one positive and one counterexample, as fixtures with expected outputs written
by the researcher (or accepted by the researcher after reading them).

### 6.2 Product 2 — independent expert cases

* The researcher (and, if available, a second reader) writes **≥ 20 cases** from their own field knowledge: a short claim, a short competitor
  description or real passage, the expected facet coding, and the expected precedence. Cases are written **without** running the table; the form
  has no precedence column pre-filled.
* Cases must cover: same mechanism / other scope; same observation / other explanation; true pre-emption; conjecture-only prior; pooled subsamples;
  similar-but-different measures; causal language on correlational designs; a contested finding; a correction already published.
* Procedure: apply the table to the **expert's own facet coding**. Every case where the table's output differs from the expert's expected precedence
  is reviewed: either the table is wrong (revise the row, record why) or the case reveals a facet the table lacks (add it) or the expert revises the case
  (record why). The table is accepted for production when all disagreements are resolved and recorded; none may be resolved by silently editing the case.
* This is what tests the *scientific* rule; synthetic fixtures only test that the code follows the rule.

### 6.3 Product 3 — invariant (metamorphic) checks

| Id | Invariant | Test idea |
|---|---|---|
| I-1 | Credibility never changes precedence | for every pair fixture, vary `inference`/`reproduction` annotations; precedence identical |
| I-2 | Adding a competitor never removes a PRECEDES of another competitor | add pairs; the earlier PRECEDES remains; claim status cannot move from CHALLENGED to any other status by adding sources |
| I-3 | A corpus snapshot change makes every absence-based conclusion non-current | change the snapshot; every NOT_CHALLENGED / INSUFFICIENT / NA-based value becomes `STALE_FOR_CORPUS` until screened |
| I-4 | A negative value without an inspection record is refused | store `NA` without record → error |
| I-5 | Missing material that could address the claim forces `U` | inspection record with `sections_missing: online appendix` → `NA` refused unless researcher-checked |
| I-6 | Monotonic coverage | widening the inspected sections can change `NA` to a positive value, never a positive value to `NA` |
| I-7 | A claim's precedence depends only on that claim's pairs | editing claim A never changes claim B's values |
| I-8 | Author labels do not set design facets | changing P's wording "causal" ↔ "associated" with the same described design leaves `evidence_rel` unchanged (fixture-level; coded by the researcher in expert cases) |
| I-9 | Table totality | every admissible facet combination for each kind matches exactly one row |

## 7. How Gate M measures this (two layers, plus end to end)

| Layer | Reference | Compared | Metric |
|---|---|---|---|
| MA1 coding | researcher's blind coding of decomposition and facets for the real pairs | model-proposed decomposition and facets | agreement per facet; confusion matrices; especially `NA` coded where reference has `SAME/PART` (hidden precedence) |
| MA2 aggregation | expert cases (§6.2) and the researcher's expected precedence on real pairs | table applied to the **reference** facets | disagreement count (should be 0 after WP14; any new disagreement on real pairs is a table issue) |
| MA3 end to end | researcher's blind expected precedence | table applied to the model facets after researcher confirmation | false precedence rate, missed precedence rate, per claim kind |

## 8. Notes on the rows the reviewer asked about

* **Row-7 case (same mechanism, another market, no rationale).** Draft decision: the `MECHANISM` claim "X explains Z" is PRECEDED (P5): the earlier
  paper proposed X. The `FINDING` claim "X holds in Vietnam" is BUILDS_ON (P9) with `NEW_SETTING_WITHOUT_RATIONALE`: not preceded, but flagged as weak
  unless a reason is given. If the candidate states "Vietnamese institutions (Y) make this a test of X's universality", that is a `BOUNDARY` claim, judged
  by P11–P14; it is undetermined until P's subsample reporting has been checked. The candidate summary is `MIXED` and the decision report suggests
  restating the contribution around the boundary claim. Nothing about the candidate is rejected as a whole.
* **Former rows 12–15 (SCOPE locus).** Replaced by `FINDING` (P7–P10, scope coverage) and `BOUNDARY` (P11–P14, condition tested separately). The
  question whether a flawed earlier test of the boundary pre-empts it is answered by the review: it does (P11 PRECEDES), and the flaw becomes the
  residual opportunity "a credible boundary test" (§4.1). `POOLED` is new: a sample that contains Y-cases without a separate result does not test the
  boundary.

## 9. Claim and candidate statuses

Claim status (vocabulary of the original specification), per claim, bound to the corpus snapshot:

```text
def claim_status(claim, pairs, coverage):          # pairs bound to claim revision and current snapshot; stale pairs excluded
    confirmed = [p for p in pairs if p.confirmed]
    if any(p.precedence == PRECEDES for p in confirmed):           return CHALLENGED
    if any(p.precedence == PARTIALLY_PRECEDES for p in confirmed): return PARTIALLY_ADDRESSED(remaining, unconfirmed=len(pairs)-len(confirmed))
    if len(confirmed) < len(pairs):                                return NOT_ASSESSED(confirmed, of=len(pairs))
    if coverage.reassessment_pending:                               return STALE_FOR_CORPUS(progress)
    if coverage.below_floor or any(p.precedence == UNDETERMINED for p in confirmed): return INSUFFICIENT_COVERAGE(reasons)
    return NOT_CHALLENGED_IN_SEARCHED_CORPUS(snapshot)
```

Candidate summary (never replaces the claim list): `ASSESSMENT_INCOMPLETE` if any claim is NOT_ASSESSED or STALE; else `ALL_CLAIMS_CHALLENGED`;
else `NO_CLAIM_CHALLENGED_IN_SEARCHED_CORPUS` if every claim is NOT_CHALLENGED; else `INSUFFICIENT_COVERAGE` if no claim is challenged and at least one
is insufficient; else `MIXED`. Exports show the claim table first, the summary second.

## 10. Fixtures with expected outputs (revised from Amendment 1 §A10)

| Fx | Claims of the candidate | Competitor P (facets, confirmed) | Expected per claim | Summary | Guards against |
|---|---|---|---|---|---|
| F1 | MECHANISM M2 for fraud persistence | same population/period, mechanism M1 (`REL`) | M2: RELATED → NOT_CHALLENGED (others clear) | NO_CLAIM_CHALLENGED | false precedence |
| F2 | MECHANISM X (US evidence exists elsewhere); FINDING "X in VN" | P: X proposed and tested in US (`SAME`; scope `DIS`) | MECH: PRECEDES → CHALLENGED; FINDING: BUILDS_ON + flag | MIXED | whole-candidate rejection (review 2.1) |
| F3 | F2 + BOUNDARY "X fails under state ownership" with rationale | P pooled sample includes state-owned firms (`POOLED`) | MECH: CHALLENGED; FINDING: BUILDS_ON; BOUNDARY: BUILDS_ON → NOT_CHALLENGED | MIXED | boundary lost |
| F4 | BOUNDARY "K fails in small firms" | P reports K separately for small firms (`TESTED`) | PRECEDES → CHALLENGED | ALL_CLAIMS_CHALLENGED | — |
| F5 | BOUNDARY as F4 | P studies large firms only (`NOT_TESTED`) | BUILDS_ON → NOT_CHALLENGED | NO_CLAIM_CHALLENGED | base finding as precedence |
| F6 | FINDING K in S | P finds not-K in S (`OPP`) | CONTESTS → NOT_CHALLENGED + `CONTESTED` | NO_CLAIM_CHALLENGED (flag) | disagreement as precedence |
| F7 | FINDING K in S | P: `SAME`, `COV`; P's inference confirmed NOT_SUPPORTED (leakage) | PRECEDES → CHALLENGED; residual "credible test" suggested | ALL_CLAIMS_CHALLENGED | quality deciding precedence (review 2.2) |
| F7b | F7 + CORRECTION "P's result uses look-ahead features" | P2: no critique (`NONE`); P3: same critique (`SAME_CRITIQUE`) in another fixture variant | CORRECTION: variant a NOT_CHALLENGED; variant b CHALLENGED | MIXED / ALL | — |
| F8 | EVIDENCE quasi-experimental test of K | P correlational (`OTHER_TYPE`) | BUILDS_ON → NOT_CHALLENGED | NO_CLAIM_CHALLENGED | — |
| F9 | EVIDENCE as F8 | P same staggered design (`SAME_TYPE`) | PRECEDES | ALL | — |
| F9b | EVIDENCE as F8 | P cross-sectional, labelled "causal" | `OTHER_TYPE` → BUILDS_ON | NO_CLAIM_CHALLENGED | author labels (I-8) |
| F10 | METHOD M for Q | P used M for Q (`SAME`) | PRECEDES | ALL | — |
| F10b | METHOD M for Q | P used a tone dictionary (`SIMILAR`) | PARTIALLY_PRECEDES | MIXED/partial | — |
| F11 | MECHANISM X | P text held lacks the appendix the paper cites for its mechanism tests | `U` (I-5) → UNDETERMINED → INSUFFICIENT_COVERAGE | INSUFFICIENT_COVERAGE | absence over incomplete text |
| F12 | MECHANISM about "financial reporting fraud" | P uses "earnings manipulation"/"AAER" only | with R2: `SAME` → CHALLENGED; R2 disabled: not retrieved → NOT_CHALLENGED@S with coverage listing R2 disabled | ALL / NO_CLAIM | terminology drift |
| F13 | MECHANISM X | P proposes X; model codes `NA` (wrong) | before correction: NOT_ASSESSED; researcher corrects to `SAME` (inspection record shows the passage); → CHALLENGED | ALL | hidden precedence (MA1) |
| F14 | FINDING K in S | P\* withheld, then imported (BM25 rank > k) | S1: NOT_CHALLENGED@S1 → STALE_FOR_CORPUS → after screening CHALLENGED@S2 | — | corpus invalidation |
| F15 | several claims over a crowded corpus (two terminology families, one erratum, two competing findings) | — | contradiction landscape entry; erratum makes affected pairs NOT_ASSESSED; statuses as per rows | — | realism |
| F16 | cold-start cases A1–A4 and exploratory mode | — | see Amendment 2 §B3 | — | — |

## 11. Open questions for the owner (to settle at the WP14 review)

1. Is P5 right that *proposing* a mechanism (with or without a test) precedes a later proposal of it? Or should a mechanism only be preceded by a
   paper that also tests it (then P5 needs `claim_rel` split into "proposed" and "tested")?
2. Is `PART` for "conjecture / future-research remark" right, or should such remarks be `REL`?
3. Should `FINDING` in a disjoint scope ever be `PARTIALLY_PRECEDES` (e.g. adjacent markets), or always BUILDS_ON plus the significance flag?
4. Which further claim kinds does your field need (e.g. `MEASUREMENT_VALIDATION`, `CONCEPTUAL_INTEGRATION`, `THEORY_TEST`)?
