# Structured evidence fields (9.2, Phase 3A.1)

The deterministic workflow executors decide only from **structure**: the fields below, graph relations, and explicit
author flags. The wording of a summary, quote or abstract never decides a status, a classification, what is reopened or
whether evidence supports or challenges a claim. Words that may deserve a reader's attention are listed under
`review_flags` (`kind: lexical_cue`, `effect: none`) and change nothing (invariant I1).

The fields are optional conventions inside the `content` of existing object types: no schema change, nothing to migrate.
A missing field is reported as missing and the assessment that needs it is `NOT_ASSESSED`; nothing is defaulted. Numbers
must be numbers: `"p<0.01"` is not a p-value and is reported as invalid.

| Object | Fields |
|---|---|
| `Claim` | `predicted_direction` (`positive` \| `negative` \| `no_effect` \| `nondirectional`), `focal_variable`, `outcome_variable`, `hypothesis_label`, `scope` / `boundary_conditions` |
| `Result` | `hypothesis_ids[]`, `estimate`, `std_error`, `p_value`, `ci` `[lo, hi]` + `ci_level`, `alpha`, `n`, `estimator`, `run_id`, `artifact_sha256`, `provenance` (`VERIFIED_RUN` \| `AUTHOR_REPORTED` \| `IMPORTED_FROM_PAPER`), `contradictory` (author flag) |
| `Analysis` | `status` (`PLANNED` \| `EXECUTED`), `purpose` (`main` \| `robustness` \| `placebo` \| `heterogeneity`), `identification_strategy`, `assumptions[]`, `specification` `{controls, fixed_effects, clustering}`, `addresses[]` (mechanism ids), `hypothesis_ids[]` |
| `Dataset` | `structure` (`panel` \| `cross_sectional` \| `time_series` \| `experimental` \| `qualitative` \| `mixed`), `unit`, `time_index`, `data_sha256`, `data_role` (what the dataset is for in the design, e.g. `label_source`, `sample`) |
| `EvidencePassage` | `evidence_type`, `assessment.relation` (`supports` \| `challenges`), `contradictory` (author flag) |
| `Source` | `study_design`, `sample_context`, `time_period`, `unit` |
| `Adjudication` | `subject.object_id`, `scope` (`claim_support` \| `identification` \| `measurement` \| `novelty`), `verdict` |

**Field names to avoid.** The proposal gateway rejects any key named `role`, `roles`, `actor`, `actor_id`, `verified`, `revision`,
`timestamp`, `token`, ... anywhere in a proposal, because the server assigns identity, time, approval and verification. A dataset's
function in a design is therefore `data_role`, never `role`.

## Relations

`supports` / `challenges` are read **in either direction** between a claim and an object: the store does not enforce which
end is the evidence, so a link is a link. A `Result` also bears on a claim through `hypothesis_ids`, or a `tests` /
`depends_on` / `supports` / `challenges` relation.

## Classifying a result against a hypothesis

`classify_against_hypothesis` uses only `predicted_direction`, `estimate`, `alpha` and `p_value` (or a `ci` whose declared
`ci_level` equals `1 - alpha`). There is **no default alpha**.

| predicted | significant, same sign | significant, opposite sign | not significant |
|---|---|---|---|
| `positive` / `negative` | `CONSISTENT` | `CHALLENGES` | `INCONCLUSIVE` |
| `no_effect` | `CHALLENGES` | `CHALLENGES` | `COMPATIBLE_NOT_ESTABLISHED` |
| `nondirectional` | `CONSISTENT` | `CONSISTENT` | `INCONCLUSIVE` |

A non-significant estimate never refutes a directional prediction and never establishes a null. A missing or invalid input
gives `NOT_ASSESSED` and names what is missing.

## What reopens a contribution (Discussion)

Only: a result classified `CHALLENGES`; a result the author flagged `contradictory: true`; a `challenges` relation between a
result or passage and a claim. Each item records its basis (`classification`, `author_flag:contradictory`,
`relation:challenges`).

## Verification of results

A `Result` is `VERIFIED_RUN` only if its `run_id` is a succeeded execution run in the run registry and its
`artifact_sha256` equals that run's recorded output digest. Writing `provenance: VERIFIED_RUN` or passing
`run_metadata={"verified_computation": True}` verifies nothing; the former is reported as `CLAIMED_VERIFIED_RUN_NOT_FOUND`,
the latter under `ignored_assertions`.
