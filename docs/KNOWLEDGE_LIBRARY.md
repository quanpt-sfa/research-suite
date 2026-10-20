# Knowledge infrastructure (9.2, milestone M2)

What this adds: papers become stored, sectioned text with verified passages; reading protocols extract from them with every quote checked; an evidence-claim check asks whether a statement is a fair reading of a passage; and a shared library holds reusable knowledge that a project imports whole, so it keeps working when the library is gone.

Plan: `docs/refactor_9_2/RESEARCH_SUITE_9_2_REFACTOR_PLAN.md` (phases 3C.0, 5b.corpus, 3A.2, 4.2, 4.3, 4.4). Acceptance: `docs/refactor_9_2/milestone_report.md` (generated from pytest results).

## 1. Corpus (`knowledge/corpus.py`)

| Command | Does |
|---|---|
| `research corpus import --project P --file paper.pdf --citation-key K` | Stores the text in the content-addressed store (never inline), records its hash, stores the detected section structure as a sidecar. `.pdf` needs `pypdf`; `.txt`/`.md` need nothing. The paper stays on this machine. |
| `research passage add --project P --source SRC_K --quote "…"` | Creates an `EvidencePassage` with the exact span. Refused (`QUOTE_NOT_IN_SOURCE`) if the quote is not in the stored text. |
| `research passage verify PASSAGE` | Re-checks a passage against the stored text of the source revision it points to. |

Add `--library` to put the object in the library instead of a project (ids then get the `LIB_` prefix).

A passage is a *location in a source*. It says nothing about whether a statement built on it reads it correctly; that is section 3.

## 2. Reading protocols (`workflows/reading_protocols.py`)

`RP_INTRO_ARGUMENT`, `RP_THEORY`, `RP_METHOD`, `RP_CONTRIBUTION` are data (`workflows/protocols_data/reading_protocols.json`): the fields to extract and the comparison dimension whose sections are read. `research extract --project P --source SRC_K --protocol RP_METHOD --model <id>` (needs the optional Anthropic SDK; nothing contacts a model otherwise):

* only the sections of that dimension are shown to the model; a paper with no recognisable headings is read whole and the run says so (`section_fallback`, context `PARTIAL`);
* a text larger than the budget is split into chunks that each fit; nothing is truncated;
* a record is kept only if it names a field of the protocol and its quote occurs verbatim in the text the model was shown (offsets are computed here, never taken from the model). Others are listed under `rejected` with the reason;
* kept records become `EvidencePassage` objects pointing at the Source revision. The model's `value` is stored as `generated_by: model:…` apart from the verbatim `quote` (I9);
* the run is registered; a repeat is served from the registry, keyed by (source revision and text, protocol@version). The key omits the model on purpose; use `--no-cache` to re-extract;
* an unusable answer (not JSON, wrong schema, refusal, truncation) is a `FAILED` run that stores nothing and is never reused.

## 3. Evidence–Claim Adjudication (`workflows/eca.py`)

One ECA record is an `Adjudication` (`content.kind = "evidence_claim"`) about a statement and a passage:

* `source_match` (deterministic); `qualifier_cues` (a fixed list of conditional-wording patterns: flags, never verdicts);
* `passage_proposition`, `equivalence`, `dropped_conditions`, `cue_handling`, `verdict`: one adversarial model call;
* `guard`: a consistency rule that moved the verdict, if any. The guards only move toward `UNRESOLVED`: `EQUIVALENT`/`SUPPORTED_AS_STATED` with an unaccounted-for cue, a dropped condition, or a broader/different equivalence becomes `UNRESOLVED`.

The model only proposes. `research adjudicate run` writes `generated_by: model:…`, unconfirmed. The researcher's `adjudicate confirm` (author or owner token, with a rationale) is a second, append-only record. A record belongs to the exact words it read: editing an attribute of the object keeps it valid; editing the statement's text means it no longer applies.

| Statement state | Assessment |
|---|---|
| no adjudication, or only a model proposal | at most `PROVISIONAL` |
| a supporting verdict confirmed by the researcher | `VERIFIED_WITHIN_SCOPE` (HIGH-risk statements need this, whoever wrote them: I16) |
| `NOT_SUPPORTED`, or a passage joined by a `challenges` edge | `CONTESTED` |
| `SUPPORTED_IF_QUALIFIED` | nothing is verified as written: revise the statement so its text carries the qualification, then confirm with `--qualification-revision N` |

`research adjudicate queue` lists HIGH-impact items waiting for a decision; `research adjudicate status` shows what a statement may claim and why.

Limits: counter-evidence is read from the graph (`challenges` edges), not retrieved by similarity; the cue list is not exhaustive (a missed cue is exactly what the confirmation step is for).

## 4. The library (`knowledge/library.py`, `closure.py`)

`<home>/LIBRARY` is an ordinary store (`research library create` prints its owner token). Every object in it has a `LIB_` id; a project cannot create such ids through a proposal. An item is referred to by a pin, `LIB:<id>@<revision>#<16 hex of the content digest>`: a changed item no longer resolves.

`research library use --project P --pin PIN` imports the item with its **whole closure** in one transaction: objects reachable by edges, objects named by pins in any content field, the adjudications about any member, every referenced stored file (full texts, section sidecars, policy snapshots, replication data), and all earlier revisions, so revision numbers and digests are identical in the project. A missing or corrupt dependency aborts everything. A manifest (`KnowledgeItem{kind: library_import_manifest}`) records the digests.

* `research library verify-closure --project P` checks the project against the manifest. It never opens the library.
* `research library check-updates --project P` reports newer revisions and restricted/superseded/withdrawn items; a missing library is the status `LIBRARY_UNAVAILABLE`, not an error.
* A project whose library directory was renamed away still verifies, evaluates its domain workflow, and reproduces its replication outputs (tested in a separate process that fails if the library is opened).

## 5. Lifecycle (`knowledge/lifecycle.py`)

`acquire` (candidate, nothing checked) → `formalize` (schema of its kind; every element needs an evidence basis) → `validate` (computes the dimensions now) → `promote` (owner) → `match` (reuse) → `revise`.

Three dimensions, computed and recorded separately:

| Dimension | Computed from |
|---|---|
| `source_fidelity` | cited passages verbatim in stored sources → `QUOTE_VERIFIED`; plus a researcher-confirmed supporting adjudication of each element → `MEANING_VERIFIED`; a not-supported adjudication or a challenging passage → `DISPUTED` |
| `computational_reproducibility` | linked replication packages: declared descriptive statistics of the stored data recomputed with the standard library, within tolerance. No package code is run. |
| `methodological_validity` | a list of assessments, each for one stated context, never global. `SUPPORTED_WITHIN_SCOPE` needs a basis from methodological literature, every known criticism addressed or scoped out, diagnostics where they apply, and the researcher's confirmation. |

Promotion needs an owner token, a non-empty scope from the scope vocabulary, exclusions (or an explicit "none known"), `MEANING_VERIFIED` fidelity, every adjudication resolved, and a validity assessment for the promoted scope with a methodological basis; all of it is recomputed at the moment of promotion. Usage records live in the project and never touch the library item, so use cannot raise validity. Recorded failures (`revise --record-failure`) are shown by `match` next to the item.

`match` applies the recommendation rule: `applicable`, `candidate_validity_not_established_here` (with the questions to answer), or `related_not_applicable` (with the reason: exclusion hit, scope mismatch, contested, withdrawn or superseded).

## 6. Journal policies (`knowledge/journal_policy.py`)

`research journal-policy add` stores a policy with its snapshot; the excerpt must occur verbatim in the snapshot, and an official requirement needs a source. `lookup --as-of DATE` returns what was in force; only an official requirement in force is `binding`. A published precedent never becomes a requirement (I12).

## 7. What was and was not exercised

* All model-backed steps (extraction, adjudication) were exercised against scripted adapters, including on the text of the real pilot paper when `sample/` is present. No live model call was made.
* On the pilot paper (Zavitsanos et al., ICAIF 2025, CC BY 4.0; `sample/` is local and never committed): 13 sections detected, the structure of the paper recovered; 5 qualifier cues flagged in the whole text. Two limits showed up: a numbered sub-section with no label inherits its parent's label, so "4.2 Per year analysis" is labelled `method` because its parent is "4 Experimental setup" (it is marked `inherited`, never `high`); and the trailing "Data" appendix is labelled `data_sample`.
* Reproduction recomputes descriptive statistics only. Re-running a replication package's model is a later step (phase 5b.replication).
