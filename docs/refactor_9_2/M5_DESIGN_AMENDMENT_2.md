# M5 Design Amendment 2 — response to the second design review (2026-10-22)

Status: **design proposal, not implemented.** Takes precedence over Amendment 1 and the nine M5 documents where they conflict. The scientific rule
itself is in a separate, self-contained document for direct audit: `M5_WP14_TRUTH_TABLE_DRAFT.md`. Code references: `main` = `c6397de`.

Note for the reviewer: the second review was written without `M5_DESIGN_AMENDMENT_1.md` (untracked, not attached). For the next review the owner
should attach `M5_DESIGN_AMENDMENT_1.md`, this file and `M5_WP14_TRUTH_TABLE_DRAFT.md`.

## B0. Analysis of the second review

| # | Point | Verdict | Change |
|---|---|---|---|
| 1 | Row 7 rejects the whole candidate; a boundary contribution may survive | **agree** | verdicts per *contribution claim*; candidate summary `MIXED` (WP14 draft §2, §8, §9) |
| 2 | `p_quality` must not decide precedence; separate precedence, credibility, residual opportunity | **agree** | credibility removed from the table; residual opportunities as suggestions; invariant I-1 (WP14 draft §4.1, §6.3) |
| 3 | A complete table is not a correct table; model coding can be wrong | **agree** | Gate M MA1 (coding) / MA2 (aggregation on reference facets) / MA3 (end to end); expert cases test the table (WP14 draft §6.2, §7) |
| 4 | Negative statements cannot rest on a few closest passages | **agree** | inspection record with sections inspected, missing material, search terms, scope of the negative; missing appendix forces `U` (WP14 draft §5) |
| 5 | 30 new papers must be a scheduling batch size, not a scientific threshold | **agree**; Amendment 1 allowed a prefilter to update statuses above 30, which was wrong | B5: all new sources are screened; status stays `STALE_FOR_CORPUS` with progress until done |
| 6 | Keep A5 as a permanent invariant | **agree** | I21 (B6) |
| 7 | Allow exploratory hypotheses without a corpus | **agree** | exploratory mode (B3) |
| 8 | CAS and SQLite are not one transaction; orphan blobs; a crash after the provider answered costs a retry | **agree**; the core already has the right CAS protocol (`state/artifacts.py` docstring: stage, fsync, `os.replace`, then commit) and orphan reporting (`StateStore.recover`, `validation/integrity.py` rules `ORPHAN_ARTIFACT_BLOB`, `REFERENCED_ARTIFACT_MISSING`). One weakness found: `recover` (`state/store.py` around lines 337–345) and `validation/integrity.py` (rule 9) look for digests only in top-level keys `artifact_sha256`/`blob_sha256` of object content, while nested digests exist (`knowledge/closure.py::artifact_digests_in` walks nested values). A cleanup based on that list could delete referenced blobs. There is no cleanup today, so nothing is lost yet | B7 |
| 9 | Check the provider's real limits for the budget | **agree**; not verified yet | B8 |
| 10 | D23–D25: adversarial corpus composition, locked multi-dimension rubric, controlled baselines, blind controlled evaluation | **agree**; one limit added: the researcher cannot be blind to their own ideas (B1 baseline) | B9 |
| 11 | WP14 as its own gate with three products | **agree** | B10; products in the WP14 draft §6 |
| 12 | Approval scope | accepted as stated | B11 |

## B1–B2. Claim-level precedence; credibility separate

See `M5_WP14_TRUTH_TABLE_DRAFT.md`. In short:

* A candidate holds one or more `ContributionClaim`s of kinds `MECHANISM`, `FINDING`, `BOUNDARY`, `METHOD`, `EVIDENCE`, `CORRECTION`.
* Per (claim, competitor) the model codes facets; a 22-row table gives `PRECEDES`, `PARTIALLY_PRECEDES`, `CONTESTS`, `BUILDS_ON`, `RELATED`, `UNRELATED`,
  `UNDETERMINED`; credibility is an annotation that only produces *residual opportunities* (suggested claims the researcher may add).
* Claim statuses use the original vocabulary; the candidate summary is `ASSESSMENT_INCOMPLETE`, `ALL_CLAIMS_CHALLENGED`, `MIXED`,
  `INSUFFICIENT_COVERAGE` or `NO_CLAIM_CHALLENGED_IN_SEARCHED_CORPUS`, and never replaces the claim table.
* Data-model change: `ContributionCandidate.claims[]` (each with `claim_id`, `kind`, statement, `base_claim`, scope, condition and rationale, method,
  evidence type); `CompetitorAssessment` becomes per (candidate revision, competitor revision, snapshot) with one facet set **per claim**; the researcher
  still confirms one row per (candidate, competitor), with a verdict per claim facet.

**Workload update (replaces Amendment 1 §A2.3).** One adversarial call per (candidate, competitor) codes all claims; cost grows with the number of
claims mainly on the researcher's side.

| Scenario (4 candidates × 5 competitors, ~2 claims per candidate) | Model calls (adversarial + ECA on positive HIGH facets + sample) | Researcher rows | Claim-level verdicts in those rows |
|---|---|---|---|
| typical (10 positive claim-pairs, 8 sensitive extra facets) | 20 + 18 + ~4 = **~42** | 20 | ~40 `claim_rel` + ~8 = ~48 |
| crowded (all 40 claim-pairs positive, 40 sensitive) | 20 + 80 + ~8 = **~108** | 20 | ~80 |
| sparse (2 positive, 2 sensitive) | 20 + 4 + ~1 = **~25** | 20 | ~42 |

Negative `claim_rel` values cost no ECA call but each needs an inspection record (built deterministically) and the researcher's verdict.

## B3. Cold start with exploratory mode (amends Amendment 1 §A3)

| Case | Outputs | State | What the outputs may say |
|---|---|---|---|
| A1 corpus ≥ floor | full bundle | WAITING at checkpoints | claim statuses |
| A2 no corpus, metadata off | problem, `search_plan.md`, `missing_inputs.md`, **optional exploratory ideas** | BLOCKED at `retrieve` (`CORPUS_BELOW_FLOOR`) for the assessed path; exploratory path may complete | exploratory ideas only, labelled |
| A3 no corpus, metadata on | as A2 + `acquisition_list.csv` | as A2 | as A2 |
| A4 corpus below floor | as A2; `--accept-small-corpus` (with rationale) allows assessment, which can then only yield CHALLENGED / PARTIALLY_ADDRESSED / INSUFFICIENT_COVERAGE | BLOCKED until flag or papers | never NOT_CHALLENGED below the floor |

**Exploratory mode.** `research discover explore --problem RP` generates ideas from the problem statement and the researcher's notes, with no
literature check. They are `ContributionCandidate`s with `grounding: UNGROUNDED` and every claim status fixed at `NOT_ASSESSED (no corpus)`. Rules:

* exported only in `exploratory_ideas.md`, headed "Not checked against any literature"; never in `contribution_candidates.md`, `novelty_challenges.md`
  or `decision_report.md` status columns;
* cannot be promoted to the library as anything but a `contribution_idea` with status `PROPOSED` and `epistemic_status: hypothesis`;
* when the corpus reaches the floor, `research discover ground CAND` runs the normal validation: the claims must be linked to landscape entries (gap
  basis) or are flagged `UNGROUNDED_GAP`; only then do they enter verification.

This is the same generation as baseline B0 (B9), which the export says.

## B4. Negative statements (amends Amendment 1 §A2.1)

The inspection record of WP14 draft §5 is mandatory for every negative facet. The researcher's sheet shows it before the closest passages. Text
extraction limits recorded by the corpus importer (`Source.text_extraction`, existing field) feed `text_held`; references to appendices or
supplementary material are detected in the stored text (patterns such as "online appendix", "supplementary", "Appendix [A-Z]") and listed under
`sections_missing` unless that material is held as a separate source linked to the paper.

## B5. Delta screening without a scientific threshold (replaces Amendment 1 §A4.3)

```text
on corpus change S_old -> S_new:
    for every claim with an absence-based status: status := STALE_FOR_CORPUS(S_old -> S_new, screened 0 of |delta|)
    queue = [(candidate, P) for each open candidate, each new or revised source P]        # one screening call codes all claims of the candidate
    order queue by R1+R2 score (prefilter is used ONLY for ordering)
    process in batches of BATCH (default 30 pairs, a scheduling parameter tied to the budget, not to science)
    after each batch: progress recorded; statuses stay STALE_FOR_CORPUS until every pair of that candidate is screened and confirmed
    the researcher may exclude a source from the corpus with a reason (it then leaves the snapshot and is listed as excluded in coverage)
    a claim becomes CURRENT@S_new only when its candidate's queue is empty
```

* Cost: `|open candidates| × |delta|` screening calls (e.g. 3 × 200 = 600 for a 200-paper import), plus assessments for relevant pairs. The task plan
  shows this before running and the budget applies; if the researcher does not fund it, statuses stay stale, visibly.
* CHALLENGED claims are not re-opened by corpus growth (I-2); they are re-opened only when the preceding source is revised or excluded.

## B6. Invariant I21 (permanent)

> **I21 — Reproduction does not validate.** Reproducing a result (same data and code) shows that the computation is repeatable; it does not show that
> the inference is valid. A correct reading of a passage (ECA) shows what the author reported; it does not show that the evidence permits it. Only a
> method assessment with a methodological basis can move `inference`, and no dimension lifts another.

Added to the invariant list of plan v3 at implementation time; tests: `test_reproduced_flawed_result_keeps_inference_not_supported`,
`test_eca_confirmation_never_sets_inference`.

## B7. Transactions and CAS (amends Amendment 1 §A7)

Reuse the existing protocol, make the run boundary explicit, and fix the cleanup basis:

1. **Order** (existing for CAS, `state/artifacts.py`): answer and attempt manifest are written to CAS (staging file, fsync, `os.replace`); the attempt
   index entry `staging/attempts/<uow_key>.json` (a plain file outside SQLite, a cache) points to them.
2. **Commit** (new rule): one SQLite transaction that (a) verifies that every blob it will reference exists and hashes correctly (`ArtifactStore.verify`),
   (b) writes objects, edges, run row and audit, (c) commits. A run row is written **only** in this transaction.
3. **Invariant I22:** no run row with `execution = SUCCEEDED` exists without the objects its UoW declares, and no row or object references a missing blob.
   Checked by `validation/integrity.py` (new rule `SUCCEEDED_RUN_WITHOUT_OUTPUTS`, existing rule `REFERENCED_ARTIFACT_MISSING`).
4. **Orphan blobs are possible and accepted**: crash after a CAS write and before commit, or a rollback. They are identified and reclaimed safely:
   * reference scan must be **recursive** (`closure.artifact_digests_in` over object content, run rows, proposals, attempt index, bundle manifests);
     both `StateStore.recover` and `validation/integrity.py` (orphan rule 9 and the `REFERENCED_ARTIFACT_MISSING` check) scan only top-level keys
     today and are extended before any deletion exists (item O17);
   * cleanup is an explicit command, dry-run by default, with a grace period (default 7 days) so in-flight attempts are never removed;
   * never automatic; the report lists what would be removed and why.
5. **Cost honesty:** a crash after the provider answered and before the answer reached CAS (C2) repeats the call; an adapter timeout on a request the
   provider completed may be billed twice. The design does not claim free retries; it bounds the loss to the calls in flight and records reservations (B8).
6. **Acceptance by crash injection**, on Linux CI: points C1–C6 of Amendment 1 §A7.3 plus "kill -9" (`RESEARCH_TEST_FAULT=exit:<point>` already uses
   `os._exit`) at: after CAS write; after attempt index write; inside the commit before `COMMIT`; after commit. Assertions: I22 holds; no duplicate
   objects; staged answers reused; orphan report lists exactly the blobs written by the killed attempt; registry and integrity checks clean.

## B8. Provider limits (amends Amendment 1 §A8)

A provider **capability profile** per (provider, model), stored as data with its source:

| Field | Why it matters |
|---|---|
| maximum context tokens | reservation of input |
| maximum output tokens, and whether `max_tokens` includes reasoning tokens | the output cap must be within the provider's limit; reasoning must be counted |
| usage fields reported (prompt, completion, reasoning, cache hit/miss) | which values are provider-reported vs estimated |
| billing of failed, truncated, filtered and timed-out requests | what a retry costs |
| rate limits | scheduling |

Values come from the provider's documentation (URL and date recorded) and are confirmed by a recorded probe call (costs tokens; only with the owner's
approval). **None of these values is verified at the time of writing.** Until a profile exists, the adapter uses conservative rules: output cap ≤ the
configured ceiling (64,000 default today) and ≤ the remaining budget; reasoning assumed inside output; unreported usage charged at the full reservation;
a provider rejecting `max_tokens` is a configuration error raised before the task starts.

## B9. Gate M: decisions D23–D25 as revised by the review

* **D23 corpus.** An adversarial corpus of about 15 usable full texts **as a starting point**, built to contain: papers with the same mechanism in a
  different scope; papers with the same observation and a different explanation; at least one paper that truly pre-empts a reference claim; two
  terminology families; competing findings; the Bao erratum as a revision case; one held-out competitor. 15 papers are never presented as enough to
  support `NOT_CHALLENGED` beyond that corpus; the coverage block says so.
* **D24 rubric and thresholds**, locked (stored with a timestamp) before the pilot, with separate dimensions, never one scale:
  candidate generation (novelty relative to the corpus, significance, feasibility, reasoning quality, each 1–4 with anchors), retrieval recall,
  false precedence, missed precedence, evidence fidelity (ECA-confirmed share), coding accuracy (MA1), human usefulness.
* **D25 baselines, controlled.** B0 = the same model, **the same corpus texts**, the same token budget and the same problem statement, asked in one
  prompt (long context) for contribution candidates; B1 = the researcher's own candidates written before the run. Blind evaluation: candidates of the
  suite, B0 and B1 rendered in one template, ids removed, shuffled; the rater does not know the source. **Limit:** the researcher will recognise their own
  B1 ideas, so B1 can be rated blind only by a second rater; without one, the suite-vs-B0 comparison is blind and the B1 comparison is reported as not blind.

## B10. WP14 as a separate approval gate

WP14 delivers the three products of the review (`M5_WP14_TRUTH_TABLE_DRAFT.md` §6): the table with a counterexample for every PRECEDES /
PARTIALLY_PRECEDES row; ≥ 20 independent expert cases with expected outcomes written by the researcher (not by a model); invariant checks I-1 … I-9.
Production use of the table requires: all expert-case disagreements resolved and recorded; owner approval of the table version (it is data with a
version, so later revisions re-derive statuses from stored facets). Effort: development 2 / 3 / 4 days; researcher 6–10 hours (cases and review).

## B11. Approval scope (as given in the review)

| Scope | Status | Conditions |
|---|---|---|
| WP0 CI repair | **approved by the reviewer**; starts on the owner's go | — |
| WP1 core, WP2 export | **conditionally approved** | before code: transaction contract (B7) and export contract (Amendment 1 §A9) closed; backward compatibility with M4 stores; `GATEL_REAL` and every store under `sample/` are never migrated or written by tests or tooling — only the owner migrates a copy |
| WP14 | **approved to draft and test the design** | not a production rule until audited (B10) |
| WP3–WP13 | **not approved** | opened one by one along the dependency graph, after the contracts and WP14 are approved |

## B12. Effort

Changes on Amendment 1 §A11: WP14 1.5 → 3; WP8 +1.5 (claim level, inspection records); WP5 +0.5 (batching, progress); WP1 +0.5 (recursive reference
scan, I22 check); WP12 +0.5 (controlled B0). New expected total **≈ 70 development days** (≈ 50–98); researcher **≈ 28–40 hours** (WP14 cases, blind
references, ratings, confirmations).

## B13. Superseded passages

| Document | Passage | Superseded by |
|---|---|---|
| `M5_DESIGN_AMENDMENT_1.md` | §A1.2–A1.5 (facets, 25-row table, candidate status) | WP14 draft §2–§4, §9 |
| same | §A2.1 negative facets, §A2.3 workload | B4, B1 workload table |
| same | §A3 cold start | B3 |
| same | §A4.3 delta screening above 30 | B5 |
| same | §A6 MA component, D23–D25 | WP14 draft §7, B9 |
| same | §A7 transactions | B7 (adds I22, recursive scan, cost honesty, kill tests) |
| same | §A8 budget | B8 (capability profile) |
| same | §A10 fixtures | WP14 draft §10 |
| same | §A11 effort and approval | B11, B12 |
