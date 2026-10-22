# WP6.2 design: a claim-centric representation, and how it is benchmarked

Date: 2026-10-23. Baseline: `e743849`. Status of this document: design written before the code; deviations found while implementing are listed at the end (§9).
Prior art is in `M5_WP6_2_PRIOR_ART_AUDIT.md`. Nothing here starts WP7–WP8, publishes anything, or claims novelty.

## 1. What the existing code does (answers to the owner's seven questions)

Every statement cites the file I read.

1. **Which claim does the `Claim` object serve?** The *researcher's own* claim. It has `text` and a `claim_tier` (`descriptive`, `associational`, `predictive`, `mechanism_consistent`, `causal`, `measurement_validation`, `conceptual`) and
   is created through proposals by the owner/author roles (`core/model.py` `CLAIM_TIERS`; `protocols/schema.py:84`; used by `execution/dag.py`, `knowledge/loop.py`). It is not a claim a paper makes.
2. **Can author claims be Landscape entries?** In part, and they already are: `landscape._entry` makes one entry per verified record, with `category` (`finding`, `theoretical_claim`, `interpretive_finding`, `boundary`, `stated_assumption`, `construct`…),
   `claim_kind` (empirical / interpretive / conceptual) and `provenance: "author_stated"` (`landscape.py:303-312`). **Gaps**: (a) no attribution, so "Previous studies have established X" and "we find X" are both `author_stated`;
   (b) one passage per entry (`author_claim`); (c) entry ids `E001…` are local to one Landscape, and a Landscape needs a current `RetrievalRun` for a `ResearchProblem`, so there is no per-paper inventory that exists before or outside a problem;
   (d) scope, premises, mechanism and boundary are separate entries, not attributes of the claim they qualify; (e) `RP_LANDSCAPE` caps each field at 8/8/8/4/4/5/3/4/3 records.
3. **Does `EvidencePassage` support several passages per claim?** The object is one verified span (`quote`, `char_start`, `char_end`, `source_id`, `source_revision`) (`schema.py:71`). A claim can *reference* several passages, but nothing builds that: a Landscape entry has an `evidence_basis` list that is
   always one item. ECA pairs one statement revision with **one** passage revision per adjudication, and `eca.basis_passage` / `run_pending` take only the *first* source-passage basis (`eca.py:734-741`). `eca.adjudicate(statement, passage_id, …)` accepts any passage, so multi-passage pairs are possible but are not scheduled.
4. **Source@revision versus Work identity?** Yes. A Source revision is the unit of evidence; `Source.work_id` and `version_id` group versions of one work, and `version_relations`, `first_public_date` (with `date_evidence`), `document_type`, `review_status` describe them (`corpus.py`, `schema.py:55-68`).
   `landscape.held_versions` / `latest_version` judge the latest version from the whole registry. Claims are never copied between versions; a Landscape reports a `version_difference` only for findings that carry the same relationship label.
5. **What do relation proposals store?** `discovery/relations.py`: kinds `conceptual_divergence`, `theoretical_incompatibility`, `explanatory_incompleteness`, `boundary_tension`, `complementarity`, `empirical_disagreement`, `implied_assumption` (proposable) and `contradiction`, `version_difference`,
   `definitions_differ` (rule-only). Each has sides (entry, Source@revision, passage@revision, locator), shared / differences / rationale / `missing_information` / levels, `already_addressed_by` as a hint, and `reviews` (CONFIRMED / DISMISSED / NEEDS_SCOPE with who, when, reason). Pair relations need at
   least two *works* (`SAME_WORK` refusal). **Not representable now**: SUPPORTS, CHALLENGES, QUALIFIES, DEFINES, ASSUMES, DEPENDS_ON, EXTENDS, RECONCEPTUALIZES, CONTRADICTS_WITHIN_SCOPE and SAME_IDEA_PROPOSED as such; relations *inside* one paper; claim ↔ evidence.
6. **Library reuse of an inventory without losing source and scope?** `knowledge/library.py` + `closure.py`: an object added to the library travels with its outgoing-edge closure (Source@revision, passages), library pins `LIB:<id>@<rev>#<digest16>`, all earlier revisions and the artifacts, and `use_in_project` writes all of it or none.
   A claim inventory with `derived_from` edges to its Source revisions and passages therefore carries its sources and its own scope fields into another project. Nothing in the library layer is claim-specific and none of this is added by WP6.2 beyond a test that the closure of an inventory is complete.
7. **How are extraction caches pinned?** `reading_protocols.extraction_key = Source@revision#text_sha256[:16]|protocol@version`, and the run fingerprint adds the protocol digest. **The model is deliberately not in the key** ("use `reuse=False` to re-extract"), and the chunking settings (`max_input_tokens`) are not either.
   A different model therefore silently reuses another model's cached records. Passages from an imported library Source reuse by `extraction_key` as well (`_cached_from_passages`).

Other findings that shape the design: the same paper is read by up to five protocols (`RP_INTRO_ARGUMENT`, `RP_THEORY`, `RP_METHOD`, `RP_CONTRIBUTION`, `RP_LANDSCAPE`), each selecting sections by comparison dimension, so overlapping text is read again; a landscape build runs two of them
(`RP_LANDSCAPE` reads results plus eight further dimensions plus unlabelled sections; `RP_THEORY` reads theory plus unlabelled sections). The cap is stated to the model ("At most N records"), so a compliant model truncates silently and only a model that disobeys produces an `over_cap` rejection.

## 2. Capability matrix

| Owner's requirement | Exists | Gap |
|---|---|---|
| Extract claims from full text, verified quote | yes (`run_protocol`, `quotes`) | claim-specific protocol; no caps; coverage statement |
| Own vs background attribution | no | new axis |
| Claim kind / epistemic role | partly (`claim_kind` of 3 kinds, categories) | nine roles incl. contribution, definition, assumption, boundary, limitation |
| Passages, premises, assumptions, methods, boundary conditions linked to the claim | separate entries | linked, with basis (stated in quote / model-inferred) |
| Several passages per claim, each with its reason | no | evidence links with role and `why_needed` |
| Query relations between claims | cross-work proposals only | 11 kinds, within and across papers, claim ↔ passage |
| Versions of one work | `version_difference` by label | alignment proposals with 8 change kinds, no auto identity |
| Cross-paper synthesis with provenance | Landscape | stays; inventory is its input, not a replacement |
| Benchmark of extraction quality | none | harness, metrics, annotation form; public data only where licensed |
| Reuse reads, cut tokens | per-protocol cache | one inventory + views; model-aware key; cost report |

## 3. Decisions

**D1. One new object type, `ClaimInventory` (server-only).** Proof of need: `Claim` is the researcher's claim with a tier; `Landscape` is bound to a `RetrievalRun` and a problem, its entries have local ids and one passage, and it records no attribution; `EvidencePassage` is a span with no claim semantics.
What none can hold is *a per-paper set of claims with attribution, scope and several evidence links, reusable by any problem, with its own relations and alignments*. No other type is added: claims are `elements` of the inventory (so ECA's existing `elements` convention applies), evidence is the existing `EvidencePassage`,
relations and alignments are lists inside the inventory (the same pattern as Landscape `inferences`).
An inventory covers an explicit **set of Source revisions** (id `CINV_000001`), has `derived_from` edges to each Source@revision and to each passage it cites (so library closure and invalidation work), and gets a new revision for every change (`expected_head` as in Landscape).

**D2. Two independent axes: attribution and role (vocabularies in `taxonomy/claim_inventory.json`).**
Attribution: `AUTHOR_OWN_CLAIM`, `AUTHOR_REPORTS_PRIOR_WORK`, `AUTHOR_CRITIQUES_PRIOR_WORK`, `RESEARCHER_INTERPRETATION`, `MODEL_PROPOSED_INTERPRETATION`.
Role: `EMPIRICAL_FINDING`, `INTERPRETIVE_FINDING`, `THEORETICAL_PROPOSITION`, `METHODOLOGICAL_CLAIM`, `DEFINITION`, `CLAIMED_CONTRIBUTION`, `ASSUMPTION`, `BOUNDARY_CONDITION`, `LIMITATION`, plus `GAP_STATEMENT` (the owner lists "the author identifies a gap" in §3.4 among the cases to separate,
and it is none of the nine roles of §3.1; added, and listed here so the choice is visible).
A model extraction may assign only the first three attributions; the last two are produced by the researcher or by an explicit proposal step. A claim that reports prior work cites its works as `cited_works[{citation, state}]` with state `REPORTED_BY_CITING_AUTHOR` until the researcher links a held Source (`CITED_SOURCE_LINKED`, still not checked) — the suite never assigns the content to
the cited study.

**D3. Author-stated versus model-inferred, field by field.** The wording is a verified quote. The restatement (`text`) is the model's and is judged by ECA. Every optional attribute (scope, premise, mechanism, boundary condition) is stored as `{value, basis}`: `STATED_IN_QUOTE` only if a deterministic check finds the value's content words inside the claim's quoted
passages; otherwise `MODEL_INFERRED`. An attribute the model does not give is absent (no defaults). The check is textual overlap, not meaning, and the exports say so.

**D4. Atomic and compound claims.** `granularity` is `ATOMIC` or `COMPOUND`. A compound claim stays one claim; its `components` list restates each part *with its own conditions*; the whole keeps its qualifiers, scope and conditions. Nothing is split into separate claims by the suite.

**D5. Evidence links.** `evidence[]` on a claim: `{passage_id, passage_revision, link_role, why_needed, derivation}`, `link_role` in `WORDING`, `QUALIFIER`, `SUPPORTING_EVIDENCE`, `PREMISE`, `CONTEXT`, `BOUNDARY`. Exactly one `WORDING` link exists for an extracted claim; others come from the extraction (qualifier sentences) or from a selective follow-up (D8)
or from the researcher. A claim without a verified wording quote cannot come from extraction; researcher-entered and model-proposed interpretations have no wording and are exported as `author_stated = false`.

**D6. Relations (11 kinds) and a deterministic gate.** Stored in `relations[]`: from claim, to claim *or* passage, kind, rationale, scope compatibility (levels, `COMPATIBLE | DIFFERENT | NOT_ASSESSED`), missing information, the Source@revision and passage@revision of each side, derivation and provenance, `status: PROPOSED`, `reviews[]`, `needs_reassessment`.
Gate (pure): kind in vocabulary; ends exist and differ; `SAME_IDEA_PROPOSED` needs two *works* and a stated `meaning_comparison` (never wording similarity); `CONTRADICTS_WITHIN_SCOPE` needs the shared scope stated; `DEFINES` starts at a `DEFINITION`; `ASSUMES` ends at an `ASSUMPTION`; relations between two versions of one work are refused
here and belong to alignments (D7); a model's proposal needs rationale and `missing_information`. No relation is created from similarity: a candidate finder (BM25 and construct labels) lists claims worth reading and writes nothing. Reuse: the review record, the `acknowledge_missing` rule and the
`UNCONFIRMED / CONFIRMED_BY_RESEARCHER / DISMISSED / NEEDS_SCOPE` states of `discovery/relations.py`; inspection records stay the only source of a recognition status.

**D7. Alignment of claims across versions of one work.** `alignments[]`: earlier claim, later claim (or none), change in `claim_version_change` = `UNCHANGED, QUALIFIED, NARROWED, WEAKENED, STRENGTHENED, REVERSED` (all six are in the existing `claim_change` vocabulary, tested) plus `WITHDRAWN` and `NEW_IN_LATER_VERSION`. Both ends must have
the same `work_id` and different Source revisions. A proposal, never an identity: `WITHDRAWN` and `NEW_IN_LATER_VERSION` rest on an absence, so the gate adds the later (or earlier) extraction's coverage caveat (unread sections, saturated caps) and requires `missing_information`. Claims stay at their own Source@revision; nothing is copied from a preprint to its journal version.

**D8. Extraction strategy and token use.** Three conditions are implemented and measured offline: **A** the existing protocols run independently; **B** `RP_CLAIMS@1` once over every non-reference section, then *views* (landscape-style entries, introduction-argument components, contribution records) derived from the stored records with no call;
**C** `RP_CLAIMS@1` per section group, then `RP_CLAIM_EVIDENCE@1` only for claims flagged for follow-up (no evidence beyond the wording, compound, model flag `needs_deeper_reading`, or a protocol field at saturation), reading only spans chosen deterministically. Neither B nor C is assumed better. `RP_CLAIMS` has no `max_records`; coverage is reported
as sections read / not read and chunks, and a claim count is never offered as evidence of complete reading.
Cache key: for the new protocols `Source@revision#text_digest | protocol@version | config digest (chunking, section policy, follow-up spans) | model identity` (provider, model id, adapter version, script digest). A different model, or different settings, is a different run. For the five existing protocols the key is **not changed** (existing
caches and passage revisions stay valid); instead a reuse now states in its result whether the cached model equals the requested one (`reuse_model_matches`, `cached_model`), so a cross-model reuse is visible, not silent. Decision for the owner: whether the old protocols should become model-scoped too (B7).
Costs: `llm_calls`, `input_tokens`, `output_tokens` as recorded by the adapter with `usage_source`; scripted adapters estimate tokens from the prompt text, and every table says `estimated`, never provider billing.

**D9. Benchmark harness (`benchmarks/claims.py`).** Metrics are functions over (system output, gold) with exact-span and overlap/semantic matching reported separately: claim P/R/F1, attribution accuracy and confusion matrix, evidence span P/R and overlap, claim–evidence link P/R, qualifier omission rate,
unsupported-claim rate, multi-span coverage, relation P/R, version-alignment error, tokens per source, tokens per accepted claim, calls per source, cache reuse rate. No composite score. Gold comes only from (a) SciFact dev (licensed) for evidence-sentence candidate selection, (b) small independent synthetic fixtures that test
the metric code and are labelled synthetic, (c) the researcher's own annotations through the blank form (`claim_annotation_form`), which I do not fill. Disagreement and adjudication are stored (two annotators → agreement, adjudicated gold). Gold built from a model's own output is refused by the loader (a `generated_by` check).

**D10. Exports** (`export/claims.py`, same container, manifest, closure digest and provenance classes as the other exporters): `claim_inventory.csv`, `claim_evidence_links.csv`, `claim_argument_relations.csv`, `claim_version_history.csv`, `claim_coverage_report.md`, `claim_attribution_audit.md`, `claim_graph.md` (Mermaid and a readable adjacency list),
`claim_benchmark_results.json`, `token_cost_report.csv`, `manifest.json`. Every row has Source@revision, provenance class (`data_derived`, `model_synthesised`, `researcher_confirmed`) and confirmation status. A proposal is never exported as established; a claim with no verified wording is exported with `author_stated=false`.

**D11. Attribution checks are flags, not verdicts.** Like ECA's qualifier cues: a fixed list of patterns finds citation cues ("Smith (2020) argues", "[3]", "prior studies have shown", "it has been established") and own-work cues ("we find", "this paper argues", "our results"). A model attribution that disagrees with the cues is *flagged for review* in `claim_attribution_audit.md`; the cue never changes it.

**D12. Currency.** A claim is `CURRENT` while its Source head revision equals the revision it was read at and its passages are unchanged; otherwise `SOURCE_REVISED`. A relation or alignment is `needs_reassessment` when either side is no longer current or a researcher correction to either claim's wording, attribution or role was recorded after it.

## 4. Compatibility contract

Additive only; each item has a test.

* `OBJECT_TYPES` gains `"ClaimInventory"` at the end; `SERVER_ONLY_TYPES` gains it. Existing types, order and stores are unchanged. No database migration (the objects table is generic); no store schema version change.
* `protocols/schema.py`: a `ClaimInventory` entry; `EvidencePassage` gains an optional `attributes` object. Existing passages validate unchanged.
* `reading_protocols.json`: two protocols added; the five existing ones keep their digests (`RP_CONTRIBUTION@1 84cf1a55…`, `RP_INTRO_ARGUMENT@2 fb259332…`, `RP_LANDSCAPE@2 cc12296e…`, `RP_METHOD@4 bc905a5c…`, `RP_THEORY@3 5790fcac…`, all recorded before any change). New `ReadingProtocol` fields enter the digest only when set.
* `run_protocol` gains optional keyword arguments (`spans`, `context`, `model_scoped`); the default path and the key of an existing protocol are byte-identical; a result gains additive keys (`cap_saturated`, `reuse_model_matches`).
* `eca.iter_statements` lists `ClaimInventory` elements (adds adjudication workload exactly as Landscape did, backlog B4).
* Taxonomy: a new file; `claim_change` is untouched.
* Landscape, retrieval, problem, review-protocol code is not changed, except that the Landscape coverage reports `fields_at_cap` (a new key).

## 5. What is deliberately not built

A graph database; an ORKG or nanopublication client or export; any publication; automatic merging of claims of different authors; automatic equivalence from similarity; a GROBID adapter (no evidence it helps; not run); a trained attribution tagger; any paid model run (B1 is open); WP7–WP8; WP14.

## 6. Test plan (written before the code)

1. Schema and compatibility: registration, server-only, existing digests, old Landscape still builds and reads, existing passage validates, `claim_change` ⊇ the six reused terms.
2. Attribution: "Smith (2020) argues…" stays `AUTHOR_REPORTS_PRIOR_WORK` with `REPORTED_BY_CITING_AUTHOR`; a paper that moves from reporting to arguing yields both attributions; cue/model disagreement is flagged, never overwritten; the model cannot assign the researcher/model interpretation kinds; no content is copied to a cited Source.
3. Fields: stated-in-quote versus model-inferred; no default values; compound claims keep conditions; a claim whose quote is not in the text is rejected (no author-stated claim without a quote).
4. Multi-passage: several links with distinct roles; each verifies; a claim is not forced to one quotation.
5. Relations: all 11 kinds; gate refusals; proposed until reviewed; confirmation does not change provenance; needs-reassessment after a source revision or a researcher correction; similarity never creates a relation.
6. Versions: alignment proposals, each of the eight changes; same-work and different-revision enforced; absence-based changes carry the coverage caveat; no copying of claims between versions.
7. Cache: model change is a new run; same model and settings is a cache hit; changed chunking is a new run; a reused old-protocol result states the model match.
8. Coverage: no cap on `RP_CLAIMS`; unread sections are reported; a saturated cap in `RP_LANDSCAPE` is reported.
9. Export: files, manifest, closure, verify, tamper detection, provenance classes, no unverified claim exported as author-verified, deterministic bytes.
10. Benchmark: each metric on hand-checked cases; exact versus overlap; SciFact loader and licence check; gold from a model's output is refused; `DATA_UNAVAILABLE` where there is no data.
11. Cross-design: the three fixtures (critical review, education, accounting fraud) share one Research Core and use no design's rule for another.

## 7. Status labels used in the report

`SCHEMA_TESTED` (tests on synthetic fixtures), `EXTRACTION_BENCHMARKED` (a component measured against licensed independent gold: only the evidence-sentence candidate selector, on SciFact dev), `SCIENTIFICALLY_VALIDATED` (needs real sources and independent expert judgements: not claimed),
`REAL_CORPUS_VALIDATION_PENDING`, `DATA_UNAVAILABLE`.

## 8. Risks

A model may mislabel attribution in the introduction of a conceptual paper (the cue check only flags); textual overlap for `STATED_IN_QUOTE` is crude (a paraphrase will read as inferred, which errs on the safe side); claim counts differ by model and strategy and are not a quality measure; offline scripted runs measure the pipeline and cost mechanics,
not what a real model would extract.

## 9. Deviations found while implementing

Found while implementing or measuring; each is a change to the text above, made for the reason given.

1. **The follow-up policy (D8) was wrong as first written.** I flagged every own finding with nothing beyond its wording and let `followup` read all flagged claims over the best-matching *paragraphs*. Measured on an
   invented long paper (about 165 findings) that made 183 model calls and read the same characters about 145 times. Now: the flag stays in the report (`NO_EVIDENCE_BEYOND_WORDING`) but costs no call; a call is made only
   when the model asked for deeper reading or the claim is compound (`FOLLOWUP_REASONS`); the text read is the best-matching *sentences* with their neighbours, at most about 2,500 characters; `--max-claims` is a budget
   and lists what it leaves out. The figures in the report are from the corrected policy; the first measurement is recorded here because it is a finding about the design.
2. **The basis check keeps numbers.** The retrieval tokenizer drops digits, so a literal period such as "2013 to 2016" read as inferred. `claims.content_words` keeps them. The check remains a word-overlap test; a span of
   years built from two spans the text gives would read as stated (documented in `CLAIM_INVENTORY.md`).
3. **One standing alignment per pair of claims** (not in D7). A model proposed REVERSED and UNCHANGED for one pair and the gate accepted both. Now a second change for a pair is refused (`PAIR_ALREADY_ALIGNED`) unless the first
   was dismissed.
4. **A fourth provenance value** in relation and alignment tables: `researcher_proposed_unconfirmed`. The export contract has three classes; a researcher's own proposal that is not yet decided fits none honestly.
5. **No path to publish an inventory into the knowledge library.** Library objects carry `LIB_` ids and an inventory's claims cite passage ids; only the edges (`derived_from` every Source revision and passage) that a
   closure would walk are in place and tested. Answer to question 6 of the audit: reuse across projects is possible in principle and not built.
6. **Additive keys in `run_protocol` results** beyond those listed in section 4: `ranges_read`, `chars_read`, `chars_total`, `sections_not_read`. They are what the re-reading measurement needs.
7. **`relation_eval` was dropped** from the benchmark module: relations are scored in `evaluate`, where the claim matching across sources is known.
8. **GROBID and the other public benchmarks.** No GROBID adapter (Docker is not available here, nothing was run). SciArg could not be fetched; CLAIM-BENCH and SciArg were not used for licence reasons.
   SciFact was downloaded (3,115,079 bytes, sha256 recorded) into an ignored directory and used for the one task it fits.

