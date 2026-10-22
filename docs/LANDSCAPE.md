# Landscape (M5 WP6, extended by WP6.1)

A landscape is a table of what the papers on a **current** reading list state, assume, measure and admit, built so that a later step (candidate generation, WP7)
can start from it without trusting anything the suite did not verify. It compares papers; it never says which one is right.

```bash
research landscape build RET_000001            # read the reading list (model calls; every record quote-verified)
research landscape show LAND_000001            # entries by type, three layers each, suite inferences, versions, currency
research landscape review LAND_000001 I001 NEEDS_SCOPE --reason "..."      # your decision on an inference
research landscape assess LAND_000001 E008 --verdict LIMITED --criteria c.json --rationale "..."   # your method assessment
research landscape link-reproduction LAND_000001 E008 --kind package_run --package PKG_X --outputs pos --reason "..."
research landscape propose LAND_000001        # a model proposes relations between studies and implied assumptions (WP6.1): proposals only, checked by a gate
research landscape add-proposal LAND_000001 --proposal p.json --provenance critic_proposed --critic "Reviewer 2"
research landscape candidates LAND_000001 I003 # held sources that may already address a proposed tension (words only, no model): papers to read, not evidence
research landscape inspect LAND_000001 I003 --results r.json --rationale "..."   # your inspection of them: the only basis of a recognition statement
research review-protocol set|adopt|screen|audit PROB_000001 ...   # the method of the review as a record (docs/REVIEW_PROTOCOL.md)
research export landscape LAND_000001          # research_landscape.md, evidence_table.csv, works_and_versions.csv, relations.csv + manifest
research task start --goal build_landscape --statement "..." --params '{"retrieval_id": "RET_000001"}'     # the same, as a task with checkpoints
```

## What is read, and from what

`landscape build` refuses a run that was not searched, whose problem or accepted terms changed since (`RETRIEVAL_PROBLEM_STALE`, `RETRIEVAL_CONFIG_STALE`), or whose
corpus changed (`RETRIEVAL_CORPUS_STALE`) unless you pass `--allow-stale-corpus` to build on the snapshot the search saw. A source revised since the search is never
read as the revision the search described (`LANDSCAPE_SOURCE_CHANGED`). Only full-text sources are read; papers held as abstract or metadata only are counted as
*possibly relevant, not read* and appear nowhere as evidence.

Each paper is read at one **Source@revision** with two protocols: `RP_LANDSCAPE@2` (finding, theoretical claim, interpretive finding, boundary condition, identifying assumption,
measurement, design, stated limitation, open question) and `RP_THEORY@3` (theory invoked, mechanism, construct definition, stated assumption, assumption tested). Both read the sections
whose heading matches no label, because conceptual papers name their sections after their ideas. A record is kept only if its quote occurs verbatim in the stored text (the existing
reading-protocol rule); the others are counted under `records_rejected_in_extraction`. A failed extraction stops the build and writes nothing; what was already extracted
is reused when you build again.

## Entries and their three layers

An entry has a type (theory, construct, mechanism, stated_assumption, design, measurement, identifying_assumption, theoretical_claim, finding, interpretive_finding, boundary,
method_limitation, unresolved_question), a restatement (`text`, a model's words), `provenance: author_stated` (an entry is always what an author wrote), and for a claim a `claim_kind`:
`finding` is an **empirical** claim, `interpretive_finding` an **interpretive** one, `theoretical_claim` a **conceptual** one. The kind belongs to the claim in the paper, not to the project:
a landscape for any project reads papers of every design. Stated assumptions carry `item_type` `stated` or `tested_in_source` (the paper says it tests it).

| Layer | What it is |
|---|---|
| `author_claim` | the verbatim quote, its place in the paper and its EvidencePassage (id and revision) at that Source@revision; status `AUTHOR_ASSERTED` |
| `evidence` | for a claim (empirical, interpretive or conceptual), three **separate** dimensions (below); other entries carry `reading` only |
| `suite_inference` | the ids of inferences the entry is part of (below) |

### Evidence dimensions (A5)

| Dimension | Values | What moves it |
|---|---|---|
| `reading` | `AUTHOR_REPORTED`, `READING_CONFIRMED` | ECA on the entry (`research adjudicate run --statement LAND_x --element E008 --passage EP_...`) **and** your confirmation. A model proposal alone leaves it `AUTHOR_REPORTED`; an ECA verdict of NOT_SUPPORTED is shown as `disputed` |
| `inference` | `NOT_ASSESSED`, `METHOD_CRITICALLY_ASSESSED` with a verdict `SUPPORTED_WITHIN_SCOPE`, `LIMITED`, `NOT_SUPPORTED` | only a method assessment you write: criteria each with its basis, threats each addressed / scoped out / open, a rationale. ECA never moves it (a correct reading of a flawed design is still flawed) |
| `reproduction` | `NOT_ATTEMPTED`, `REPRODUCED_WITHIN_TOLERANCE`, `FAILED_TO_REPRODUCE`, `REPLICATED_NEW_DATA`; `NOT_APPLICABLE` for an interpretive or conceptual claim (it is not reproduced from data; linking one is refused) | a link you record to a replication package with a recorded reproduction (read from the package each time), or a new-data replication you name (the suite did not check it). Reproducing code does not move `inference` |

The verdict of an assessment keeps the shape of the rule `assess_validity` applies to a method, with the basis chosen by the claim's **route** (WP6.1): `SUPPORTED_WITHIN_SCOPE` needs a
criterion on a basis kind the route accepts, every criterion met, at least one threat (for a conceptual or interpretive claim: a rival explanation or alternative interpretation) checked and none
open; otherwise it is stored as `LIMITED` with the blockers named. `NOT_SUPPORTED` needs a criterion marked `met: no`. A model writes no assessment.

| Route | For | Basis kinds accepted for `SUPPORTED_WITHIN_SCOPE` (`basis_kind` of a criterion) |
|---|---|---|
| `empirical` | finding | `methodological_literature` (`methodological_literature: true` still works) |
| `interpretive` | interpretive finding | `methodological_literature`, `textual_evidence` |
| `conceptual` | theoretical claim | `theoretical_literature`, `logical_analysis` |

Each route also has **considerations** (measurement, design, identifying assumptions, diagnostics, limits of inference; transparency of interpretation, context, alternative interpretations, credibility,
reflexivity; conceptual clarity, logical coherence, consistency of assumptions, rival explanations, correspondence with prior theory, boundaries of applicability; and for proposed relations: faithful
reconstruction of the prior literature, evidence that an attributed assumption exists, fair treatment of competing interpretations, the reasoning from tension to reconceptualisation). They are prompts
shown in the view and the export, never a mandatory checklist, and no composite scientific score is computed from them (there is no rubric or validation for one). Nothing is ever displayed as "established" or "supported" without the three values beside it.

**The verdict and the check of its basis are kept apart.** A criterion may cite its basis only in words, or as `basis_refs` (`[{kind: source|passage, ref, revision, excerpt, supports_only}]`) or as an `argument` record (`{premises, steps: [{from, infers}], conclusion, rival_assumptions | no_rivals_reason}`). The verdict is the researcher's judgement (`RESEARCHER_ASSESSED`, `INFERENCE_JUDGMENT`). The suite's check of the basis is recomputed whenever the assessment is shown (`discovery/basis.py`): `BASIS_NOT_TRACEABLE` (a sentence only; every assessment recorded before this existed is shown so and is not upgraded), `BASIS_TRACEABLE` (the source, passage or argument record is identified), `BASIS_CHECKED` (deterministic checks passed: the passage is quote-verified at its source's current revision, the excerpt the criterion relies on is verbatim inside it and the limit of what it supports is stated; or the argument's steps cite existing premises or earlier steps, a conclusion is given and rivals are listed or their absence justified), `BASIS_CHECK_FAILED` and `BASIS_STALE` (a cited source was revised). **BASIS_CHECKED means those checks passed. It does not mean the passage supports the criterion, the argument is valid or the conclusion is right.** A basis that failed its checks does not count toward `SUPPORTED_WITHIN_SCOPE`; a sentence-only basis still counts, as before.

Landscape entries are statements ECA can adjudicate (stratum `Landscape:<type>`): the coverage policy samples them like any other STANDARD statement and raises
the rate where errors occur.

## Suite inferences, relations and implied assumptions

**By rule** (no model, `derivation: rule`, `PROVISIONAL` at most; each lists the Source@revision, direction and the conditions each side states; `scope_overlap` stays `NOT_ASSESSED`):

* `contradiction` (relation `empirical_disagreement`): findings of **different works** that name the same relationship (after normalising case and spacing, and the equivalences you declare with
  `--same A=B`) with opposite direction (positive/negative, or null against an effect).
* `version_difference`: the same, between **versions of one work**: never a contradiction between studies.
* `definitions_differ` (relation `observation`): one construct defined in different words by different works. It observes wording only; it is never converted into a divergence of meaning.

**By proposal** (WP6.1; `derivation` `model_proposed` or `researcher_proposed`; status `PROPOSED`; `UNCONFIRMED` until you decide). Whatever needs a reading of meaning is a proposal: `conceptual_divergence`,
`theoretical_incompatibility`, `explanatory_incompleteness`, `boundary_tension`, `complementarity`, a semantic `empirical_disagreement`, and `implied_assumption`. `version_difference`, `contradiction` and
`definitions_differ` cannot be proposed. A proposal comes from `landscape propose` (one model call, provenance `suite_proposed:model:<id>`) or from you (`add-proposal`, provenance `researcher_proposed`
or `critic_proposed:<name>` for a critic's proposal you record). Each goes through a deterministic gate before it is stored; a rejected item is listed with its reason and stored nowhere:

| Code | Rejected because |
|---|---|
| `PROPOSAL_KIND_NOT_ALLOWED`, `ENTRY_UNKNOWN`, `TOO_FEW_ENTRIES` | the kind is rule-only or unknown; an entry is not in this landscape; fewer than two claims (one for an implied assumption) |
| `SAME_WORK` | all entries come from versions of one work: that is a version matter |
| `FIELD_REQUIRED` | `shared`, `differences`, `rationale` (or `text`, `reasoning`), or the list `missing_information`, is missing |
| `LEVELS_REQUIRED`, `LEVELS_DIFFER` | a model proposes `theoretical_incompatibility` without the level of analysis of every entry, or the levels differ (the theories then explain different levels: propose `complementarity` or `boundary_tension`). A researcher's proposal is not held to the second check |
| `MEANING_DIFFERENCE_REQUIRED` | `conceptual_divergence` without how the meaning differs apart from the wording |
| `ADDRESSED_BY_UNKNOWN`, `DUPLICATE`, `DUPLICATE_OF_RULE` | cited an entry that does not exist; already recorded; already derived by rule |

A relation carries: the claims (entry, Source@revision, passage, place), what they share, how they differ, why, `missing_information`, `levels`, `search_terms` and `already_addressed_by`.
**Confirming** needs your reason, and, where information is missing, your acknowledgement (`--acknowledge-missing`). A model's proposal never becomes an entry (an assumption nobody wrote is never shown as an author's), never
confirms itself, and the confirmed state does not change what it is: its provenance stays.

**Recognition (remediated).** Whether the literature already addresses a tension is stated only from an **inspection record**; a model's list of sources is a hint, and an empty list is not a finding.

| Status | Means |
|---|---|
| `RECOGNITION_NOT_ASSESSED` | no inspection is recorded; nothing about absence follows (a model that named no source has assessed nothing) |
| `POTENTIAL_ADDRESSING_SOURCES` | a model named sources and nobody has inspected them (shown as unverified hints) |
| `ADDRESSED_IN_INSPECTED_SET` | you inspected sources and at least one is recorded as addressing the tension, with a verbatim quote that became a quote-verified EvidencePassage at that Source@revision |
| `NOT_FOUND_IN_INSPECTED_SET` | a stated set of candidates (found by stated terms, in the stated corpus snapshot) was inspected completely, every candidate was readable, and none addresses the tension: a statement about that set in that snapshot, not about the literature and not about novelty |
| `INSPECTION_INCOMPLETE` | a candidate was not inspected, or only an abstract or metadata is held for it (it cannot be inspected: recorded as `NOT_READABLE`); no absence statement is made |

`landscape candidates` lists held sources (full texts by BM25 over the stored sections, weaker tiers by title and abstract, and model hints) with whether each can be read: **a match is a paper to read, not evidence that it addresses the tension, and finding none is not evidence that none does.** `landscape inspect` records, per source, `ADDRESSES`, `MENTIONS_PHENOMENON_ONLY` (a mention is not a resolution), `DOES_NOT_ADDRESS` or `NOT_READABLE`, what you inspected (the whole text or which sections), a note, and for the first two the verbatim words that show it. The record keeps the tension's identity (a digest of its definition), the corpus snapshot, the terms and candidates, what was not inspected, the limitations, and your decision with reason and time. Versions of one work are judged separately: if one version addresses the tension and another does not, the status is stated with that conflict (the argument may have been withdrawn). An inspection is flagged `needs_reassessment` when the corpus changed (for a negative or incomplete one), when an inspected source was revised, or when the tension was redefined.

**What each conclusion rests on.** Every inference lists its Source@revisions; `needs_reassessment` explains why when one of them was revised, removed or excluded, and for a negative or incomplete recognition statement also when the
corpus changed since the inspection. The state you gave (for example `CONFIRMED_BY_RESEARCHER`) stays visible next to the flag.

## Versions

`work_id` groups papers for reading; evidence stays at the version that was read. `--versions retrieved` (default) reads the version the search found; `--versions all` reads every
held full-text version. For every work the landscape records the versions held now in the whole Source Registry (not only the hits), which were retrieved, which were read,
which carry entries, the latest held version by public date, and `later_version_not_read`: `true` when a later version than anything read is held (its claims are not in the
landscape), `UNKNOWN` when several versions are held and no public date says which is latest. A representative is never used to cite a claim.

## Currency

`research landscape show` and the export state five inputs, each its own status: the retrieval run's revision (`REVISED` when you set aside, restored or added papers, or searched again
after the build), the corpus snapshot, the problem input, the search configuration, and the Source revisions read. `landscape.require_current` (what WP7 will call) refuses a landscape
whose inputs moved. Rebuild with `--carry-from LAND_x` to keep your assessments, reproduction links and decisions wherever the passage and wording they concern are unchanged.

## Export

`research_landscape.md` (the inquiry, versions to know about, works and versions, findings with the three dimensions side by side, theoretical claims, interpretive findings, one table per other entry type,
inferences derived by rule, proposed relations and implied assumptions each with derivation, provenance, confirmation, recognition, missing information and reassessment flag, the method assessments (with route)
and reproduction links you recorded, considerations by kind of claim, limits), `evidence_table.csv` (one row per entry with quote and place), `relations.csv` (one row per inference) and `works_and_versions.csv`, with the common manifest
(closure over the landscape, run, problem, sources read, passages, adjudications and linked packages; provenance class per row: `model_synthesised`, `researcher_confirmed`, `data_derived`).
`research export verify` rebuilds it from the store.

## Tasks

Goal `build_landscape` with `--params '{"retrieval_id": "RET_x"}'`: S01 you confirm the reading list (checkpoint), S02 `landscape_build` (model calls, budgeted), S03 you review the inferences and any later version
not read (checkpoint), S04 `export_landscape`. If the run is not current, S02 fails with the code above and no landscape is written.

## Limits

* Only what the papers on the reading list state. A topic, competitor or contradiction that was not retrieved, or is stated in other words, is not shown to be absent.
* Introduction arguments and contribution claims of the papers are not part of this landscape (not run).
* Relations and implied assumptions are a model's reading until you confirm them; the gate checks structure (claims, fields, levels, wording versus meaning), not whether the reading is right. A model can state
  levels of analysis that are wrong, and a researcher can override: both are recorded as theirs. WP6.1 has been tested on invented papers and scripted models only (`REAL_CORPUS_VALIDATION_PENDING`).
* A model's restatement is not checked against its quote except by ECA; unconfirmed, it is only `AUTHOR_REPORTED`.
* The model reader is told to name the same relationship identically across papers; it may not. This is the main source of missed contradictions.
* Method assessments are the researcher's; there is no model-proposed assessment yet (a candidate extension, not part of WP6).
