# Landscape (M5 WP6)

A landscape is a table of what the papers on a **current** reading list state, assume, measure and admit, built so that a later step (candidate generation, WP7)
can start from it without trusting anything the suite did not verify. It compares papers; it never says which one is right.

```bash
research landscape build RET_000001            # read the reading list (model calls; every record quote-verified)
research landscape show LAND_000001            # entries by type, three layers each, suite inferences, versions, currency
research landscape review LAND_000001 I001 NEEDS_SCOPE --reason "..."      # your decision on an inference
research landscape assess LAND_000001 E008 --verdict LIMITED --criteria c.json --rationale "..."   # your method assessment
research landscape link-reproduction LAND_000001 E008 --kind package_run --package PKG_X --outputs pos --reason "..."
research export landscape LAND_000001          # research_landscape.md, evidence_table.csv, works_and_versions.csv + manifest
research task start --goal build_landscape --statement "..." --params '{"retrieval_id": "RET_000001"}'     # the same, as a task with checkpoints
```

## What is read, and from what

`landscape build` refuses a run that was not searched, whose problem or accepted terms changed since (`RETRIEVAL_PROBLEM_STALE`, `RETRIEVAL_CONFIG_STALE`), or whose
corpus changed (`RETRIEVAL_CORPUS_STALE`) unless you pass `--allow-stale-corpus` to build on the snapshot the search saw. A source revised since the search is never
read as the revision the search described (`LANDSCAPE_SOURCE_CHANGED`). Only full-text sources are read; papers held as abstract or metadata only are counted as
*possibly relevant, not read* and appear nowhere as evidence.

Each paper is read at one **Source@revision** with two protocols: `RP_LANDSCAPE@1` (finding, boundary condition, identifying assumption, measurement, design, stated
limitation, open question) and the construct and mechanism fields of `RP_THEORY`. A record is kept only if its quote occurs verbatim in the stored text (the existing
reading-protocol rule); the others are counted under `records_rejected_in_extraction`. A failed extraction stops the build and writes nothing; what was already extracted
is reused when you build again.

## Entries and their three layers

An entry has a type (construct, mechanism, design, measurement, identifying_assumption, finding, boundary, method_limitation, unresolved_question), a restatement
(`text`, a model's words), and:

| Layer | What it is |
|---|---|
| `author_claim` | the verbatim quote, its place in the paper and its EvidencePassage (id and revision) at that Source@revision; status `AUTHOR_ASSERTED` |
| `evidence` | for a finding, three **separate** dimensions (below); other entries carry `reading` only |
| `suite_inference` | the ids of inferences the entry is part of (below) |

### Evidence dimensions (A5)

| Dimension | Values | What moves it |
|---|---|---|
| `reading` | `AUTHOR_REPORTED`, `READING_CONFIRMED` | ECA on the entry (`research adjudicate run --statement LAND_x --element E008 --passage EP_...`) **and** your confirmation. A model proposal alone leaves it `AUTHOR_REPORTED`; an ECA verdict of NOT_SUPPORTED is shown as `disputed` |
| `inference` | `NOT_ASSESSED`, `METHOD_CRITICALLY_ASSESSED` with a verdict `SUPPORTED_WITHIN_SCOPE`, `LIMITED`, `NOT_SUPPORTED` | only a method assessment you write: criteria each with its basis, threats each addressed / scoped out / open, a rationale. ECA never moves it (a correct reading of a flawed design is still flawed) |
| `reproduction` | `NOT_ATTEMPTED`, `REPRODUCED_WITHIN_TOLERANCE`, `FAILED_TO_REPRODUCE`, `REPLICATED_NEW_DATA` | a link you record to a replication package with a recorded reproduction (read from the package each time), or a new-data replication you name (the suite did not check it). Reproducing code does not move `inference` |

The verdict of a method assessment follows the rule `assess_validity` applies to a method: `SUPPORTED_WITHIN_SCOPE` only with a criterion that rests on methodological
literature, every criterion met, at least one threat checked and none open; otherwise it is stored as `LIMITED` with the blockers named. `NOT_SUPPORTED` needs a
criterion marked `met: no`. A model writes no assessment. Nothing is ever displayed as "established" or "supported" without the three values beside it.

Landscape entries are statements ECA can adjudicate (stratum `Landscape:<type>`): the coverage policy samples them like any other STANDARD statement and raises
the rate where errors occur.

## Suite inferences

Derived by a stated rule from the entries, without a model; status `PROVISIONAL` at most; each lists the Source@revision, direction and the conditions each side states;
`scope_overlap` stays `NOT_ASSESSED`. Your decision (`CONFIRMED`, `DISMISSED`, `NEEDS_SCOPE`, with a reason) is recorded with who and when and does not change what the
inference is.

* `contradiction`: findings of **different works** that name the same relationship (after normalising case and spacing, and the equivalences you declare with `--same A=B`)
  with opposite direction (positive/negative, or null against an effect).
* `version_difference`: the same, between **versions of one work**: never a contradiction between studies.
* `definitions_differ`: one construct defined in different words by different works.

Limit: a conflict stated in different words for the relationship is missed unless you declare the names the same; findings without a relationship label cannot be compared
(counted in the coverage).

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

`research_landscape.md` (versions to know about, works and versions, findings with the three dimensions side by side, one table per other entry type, suite inferences with your decision,
the method assessments and reproduction links you recorded, limits), `evidence_table.csv` (one row per entry with quote and place) and `works_and_versions.csv`, with the common manifest
(closure over the landscape, run, problem, sources read, passages, adjudications and linked packages; provenance class per row: `model_synthesised`, `researcher_confirmed`, `data_derived`).
`research export verify` rebuilds it from the store.

## Tasks

Goal `build_landscape` with `--params '{"retrieval_id": "RET_x"}'`: S01 you confirm the reading list (checkpoint), S02 `landscape_build` (model calls, budgeted), S03 you review the inferences and any later version
not read (checkpoint), S04 `export_landscape`. If the run is not current, S02 fails with the code above and no landscape is written.

## Limits

* Only what the papers on the reading list state. A topic, competitor or contradiction that was not retrieved, or is stated in other words, is not shown to be absent.
* Introduction arguments and contribution claims of the papers are not part of this landscape (not run).
* A model's restatement is not checked against its quote except by ECA; unconfirmed, it is only `AUTHOR_REPORTED`.
* The model reader is told to name the same relationship identically across papers; it may not. This is the main source of missed contradictions.
* Method assessments are the researcher's; there is no model-proposed assessment yet (a candidate extension, not part of WP6).
