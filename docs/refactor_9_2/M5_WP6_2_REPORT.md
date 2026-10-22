# WP6.2 report: claim-centric knowledge representation and benchmarking

Date: 2026-10-23. Baseline: `e743849` (CI run 39777372766 green). Author: the development assistant (Claude Sonnet 5.5). For: the Research Owner.
Companion documents: `M5_WP6_2_PRIOR_ART_AUDIT.md`, `M5_WP6_2_DESIGN.md`, `docs/CLAIM_INVENTORY.md`, backlog B7.

**Status labels.** `SCHEMA_TESTED`: yes, on invented fixtures with scripted readers. `EXTRACTION_BENCHMARKED`: **only for one component**, the lexical evidence selector, on SciFact dev. A real model's claim extraction
has **not** been benchmarked. `SCIENTIFICALLY_VALIDATED`: no; it needs real sources and independent expert judgements. `REAL_CORPUS_VALIDATION_PENDING`: yes. `DATA_UNAVAILABLE`: eight of the ten benchmark tasks have no
usable public data. WP7, WP8 and WP14 were not started or approved; no paid model was called (B1 is open).

## 1. Commits and tests

| Item | Value |
|---|---|
| Initial HEAD (`git fetch`, then compare) | `e743849` = `origin/main`; CI green |
| Work commit | __WORK_SHA__ |
| CI run for the work commit | __CI_RUN__ |
| Local tests, Python 3.12 + tiktoken | 1015 passed, 2 skipped (912 before this work, plus the 1 pin-digest test of the previous commit) |
| Local tests, Python 3.10 (no tiktoken) | 1011 passed, 6 skipped (the tiktoken tests skip) |
| New tests | 7 files, 96 tests (`test_claims_schema`, `_inventory`, `_relations`, `_export`, `_benchmark`, `_strategies`, `_cli_and_compat`) |

## 2. The twelve questions

**1. What did Research Suite reuse?** Almost everything: Source@revision and `work_id`/`version_id`; `EvidencePassage` and the quote verification (`quotes`); the reading-protocol machinery (`run_protocol`: chunking,
verified quotes, caching, the run registry with its usage) extended by option, not copied; ECA (a claim inventory's `elements` are statements it can be asked about; the first passage is the wording); the review and
confirmation states and the `acknowledge_missing` rule of `discovery/relations.py`; `text_index` (BM25) for candidates; the exporter container (manifest, closure digest, provenance classes, `verify`); the taxonomy
loader; edges and transactions of the StateStore. **One new object type**, `ClaimInventory`, justified in the design (`Claim` is the researcher's own tiered claim; `Landscape` is bound to a retrieval run and a problem, has
local ids, one passage per entry and no attribution). No second store, no graph database.

**2. What did the external repositories improve?** SciArg: the own/background distinction as its own axis, and support/contradict/same-idea relations. CLAIM-BENCH: the three reading strategies as experimental conditions,
the recall-versus-cost framing, multi-evidence claims. ORKG and nanopublications: a provenance label on every statement and the refusal to overwrite (here: a new revision, a state, never a deletion). SciFact: a licensed
gold for evidence selection. GROBID: nothing (not run). None of their code or data is in the repository. Details and the **verified/inferred** distinction are in the prior-art audit.

**3. How is a claim inventory represented?** One `ClaimInventory` per set of Source revisions: `sources` (with extraction run, model, strategy, what was read and not), `elements` (claims), `relations`, `alignments`,
`coverage`, `limits`. A claim: verified **wording**; the model's **restatement** apart; **attribution** (one axis) and **role** (another); atomic/compound with per-component conditions; constructs, scope,
premises, mechanism, boundary conditions each with a `STATED_IN_QUOTE`/`MODEL_INFERRED` basis; **evidence links** with role and reason; reviews. `docs/CLAIM_INVENTORY.md` has the full description.

**4. Is the own/background distinction reliable?** **Mechanically yes; scientifically unknown.** The code keeps the three author attributions apart, never gives reported content to a cited study, flags (never overrides)
disagreement with citation cues, keeps the model's value beside your correction, and refuses to let extraction assign researcher or model interpretations. How often a real model gets the attribution right on introductions of
conceptual or social-science papers has not been measured: SciArg (the only public source of these labels) could not be fetched and has no stated licence, and no model was run. Do not read the scripted tests as accuracy.

**5. Can one claim link several passages?** Yes: any number of evidence links, each a verified span with a role (`WORDING`, `QUALIFIER`, `SUPPORTING_EVIDENCE`, `PREMISE`, `CONTEXT`, `BOUNDARY`), the reason it is needed
and its origin (`extraction`, `followup`, `researcher`). A claim is not forced onto one quotation, and a compound claim stays one claim. ECA still adjudicates a claim against its wording only (backlog B7).

**6. Does the evidence graph tell proposed from confirmed relations?** Yes. Every relation and alignment is `PROPOSED` with its derivation (`model_proposed`, `researcher_proposed`, `critic_proposed`) and
`UNCONFIRMED` until you decide; the export and the graph (dashed versus solid) show the state; a model's proposal never confirms itself; nothing is created from similarity (`SAME_IDEA_PROPOSED` needs a statement of
meaning). Eleven kinds; a deterministic gate; `needs_reassessment` after a source revision or a correction.

**7. How is claim evolution across versions tracked?** By alignments between two claims of two Source revisions of one work: `UNCHANGED`, `QUALIFIED`, `NARROWED`, `WEAKENED`, `STRENGTHENED`, `REVERSED`, and, resting on
an absence, `WITHDRAWN` and `NEW_IN_LATER_VERSION` (with the other version's coverage caveat and required missing information). A proposal, never an identity; nothing is copied between versions; the order comes from the
public dates or from your statement.

**8. Which benchmarks really exist, on how many cases, with what ground truth?**

| Benchmark | Cases | Ground truth | What it is |
|---|---|---|---|
| **SciFact dev** (licensed) | 300 claims, of which **188 have evidence**; 209 claim-abstract pairs; **366 rationale sentences**; 5,183 abstracts | SciFact's own annotators (CC BY 4.0 claims, ODC-By 1.0 abstracts); archive sha256 `11c62128…76be` | a real benchmark of **one component**: the BM25 evidence selector |
| Invented gold for one critical-review paper | 11 claims, 2 relations, plus a 3-alignment version fixture | written by hand, independent of the scripted reader | tests the metric code only; labelled `SYNTHETIC_FIXTURE` |
| Tasks 1, 2, 3, 6, 7, 8, 9, 10 on real text | 0 | none available | `DATA_UNAVAILABLE` |

**9. Precision, recall, F1 in fact?**

*SciFact dev, lexical selector, no model* (`docs/refactor_9_2/evaluation/claims/scifact_selector_dev.json`; ties broken by position; chance in brackets):

| Task | k=1 | k=2 | k=3 | k=5 |
|---|---|---|---|---|
| a rationale sentence is among the top k sentences of the evidence abstract (statistics of the one abstract, as the follow-up has) | 0.493 (0.201) | 0.746 (0.371) | 0.833 (0.517) | 0.914 (0.738) |
| the same, with statistics from all 5,183 abstracts | 0.522 (0.201) | 0.699 (0.371) | 0.818 (0.517) | 0.904 (0.738) |
| share of a claim's rationale sentences found in the top k (within-document) | 0.351 | 0.575 | 0.679 | 0.804 |

| Task | k=1 | k=5 | k=10 | k=50 |
|---|---|---|---|---|
| a cited abstract is among the top k of 5,183 (chance) | 0.713 (0.0002) | 0.883 (0.0011) | 0.942 (0.0021) | 0.968 (0.0107) |

These claims were written from the abstracts' own citation contexts, so they share vocabulary with them; read the figures as an upper-leaning indication, not as what the selector does on other papers.

*Claim extraction (P/R/F1), attribution accuracy, relation P/R, alignment error*: **cannot be computed for a real model** (no gold of this kind is available, no run is approved). On the invented gold with planted mistakes
the code reports claim P = 1.0, R = 0.909, F1 = 0.952 (10 of 11), attribution accuracy 0.9, evidence recall 0.5 and, for relations, 1 found, 1 spurious and 1 missed: this verifies the arithmetic, not any system.

**10. Did token cost fall, and was coverage traded for it?** (`strategy_cost_comparison.json`; four invented papers incl. one long one; a **rule-based stand-in**, not a model; tokens are tiktoken `cl100k` estimates, never billing)

| | A: five protocols independently | B: one `RP_CLAIMS` reading + views | C: section groups + selective follow-up |
|---|---|---|---|
| model calls | 20 | 4 (−80%) | 14 (−30%) |
| input / output tokens | 25,630 / 17,116 | 13,876 / 16,977 | 35,033 / 17,073 |
| total | 42,746 | **30,853 (−28%)** | 52,106 (+22%) |
| same characters read (summed ÷ distinct) | 1.80× | 1.00× | 1.06× |
| never read | title/abstract and references | references | references |
| records set aside for field caps (long paper) | 184; fields at cap: `finding`, `identifying_assumption`, `sample_selection` | none (no cap) | none |
| a second identical pass | 0 calls, 0 tokens | 0, 0 | 0, 0 |

B is cheaper here, C is not: each extra call repeats a 2,160-token `RP_CLAIMS` prompt (the older protocols' prompts are 597–1,184 tokens). **Coverage was traded:** one claims reading cannot fill `theory_invoked`,
`assumption_tested`, `phenomenon`, `evidence_needed`, `research_question`, `design`, and fills the eight `RP_METHOD` fields only as unclassified candidates; the older protocols leave the abstract unread and cut capped fields.
Whether a model's claims reading finds as much as five focused readings, and how faithfully, was **not measured**; neither was latency. The first design of C (follow up every unsupported finding, over whole paragraphs) made 183
calls and read the same text 145 times: measuring it is what changed the policy (design §9).

**11. What awaits scientific validation?** Attribution accuracy and claim recall of real models on the researcher's fields; the fidelity of restatements; whether the roles and the 11 relation kinds suit conceptual and
qualitative work; whether the follow-up triggers (a model asks, a claim is compound) are the right ones; the value of `STATED_IN_QUOTE`; whether strategy B loses claims the older protocols find; real provider cost; GROBID
versus `pypdf`; relations and alignments on real versions. All need independent gold the researcher (or an annotator they appoint) makes, using the blank form, and an approved bounded model run.

**12. What can WP7 reuse later?** Claims with attribution (so a candidate contribution can rest on the authors' *own* claims and not on reports of earlier work); `CLAIMED_CONTRIBUTION` and `GAP_STATEMENT` claims as HIGH-tier
ECA statements; relations with their confirmation state and `needs_reassessment` (so a candidate shows what it rests on); alignments (a claim that changed between versions); coverage statements that refuse to equate a count with
reading; the views that feed the older protocols' consumers without another read; the benchmark harness and annotation form. What WP7 must still decide: whether a Landscape is built from an inventory; the pre-emption rules for
conceptual candidates (WP14, the owner's); and that nothing here supports a novelty verdict.

## 3. What changed in existing code (all additive; each has a test)

`ClaimInventory` appended to `OBJECT_TYPES` and `SERVER_ONLY_TYPES`; schema entry; `EvidencePassage.attributes`; two reading protocols in the data file (the five older ones keep their digests, asserted);
`ReadingProtocol` options that enter the digest only when used; `run_protocol` accepts `spans` and `context` and returns extra keys (`cap_saturated`, `ranges_read`, `chars_read`, `sections_not_read`, `reuse`);
`eca.iter_statements` lists inventories; the Landscape coverage and export report `fields_at_cap`; one existing test (the set of protocols) was updated on purpose. No migration; no store schema change.

## 4. What I did not do, and why

No live model call (B1 open). No SciArg or CLAIM-BENCH data (licence). No GROBID adapter (no evidence, not run). No nanopublication or ORKG client, and nothing published. No `landscape build --from-inventory` (changes WP6 behaviour:
WP7's decision). No path to publish an inventory into the knowledge library (only the closure edges exist). `GATEL_REAL`, `sample/` and Gate K/L material were not touched.

## 5. Decisions for the Research Owner

1. Should the five older protocols become model-scoped (their caches and passage revisions re-keyed)? Today a reuse reports whether the model matches.
2. Is a claims reading (B) acceptable in place of the older protocols for the fields it can fill, given the fields it cannot (§2.10)?
3. Do you want the SciArg licence and data pursued, and CLAIM-BENCH's authors asked for a licence?
4. Who annotates gold (two annotators and an adjudicator), on which of your papers, before a bounded model run is approved?
5. Is the 11-kind relation vocabulary and the 10-role vocabulary right for your fields (`GAP_STATEMENT` was added by me)?

## 6. Limits of this report

Everything about extraction ran on invented papers with scripted or rule-based readers. The SciFact figures are the only measurement against independent human gold, and they concern a word-matching selector on biomedical
abstracts. Token figures are estimates by the tokenizer named in the result file and depend on the stand-in's answers, especially the output tokens of the long paper.
