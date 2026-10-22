# Internal retrieval (9.2, M5 WP5)

Finds what to read in the **stored corpus**. No network, no external metadata service (OpenAlex and Crossref are a later, opt-in step). Design:
`docs/refactor_9_2/M5_CONTRIBUTION_DISCOVERY_PROTOCOL.md` §5.3 and Amendments 1–2. A demonstration with its output:
`docs/refactor_9_2/demos/wp5_retrieval_demo_output.txt` (`python scripts/demos/wp5_retrieval_demo.py`).

What it produces is a **reading list** and a **coverage report**. A score ranks what to read first; it says nothing about what a paper claims. A paper that was
not retrieved is not shown to be irrelevant; a paper that was retrieved is not shown to be relevant: your review decides that.

```
research problem new ... ; research problem choose ...                  # the problem and the readings you pursue (WP4)
research retrieval new PROB_000001 --term "financial reporting fraud detection"     # fixes the queries; DRAFT; nothing is searched
research retrieval expand RET_000001            # a model proposes other names for the idea: PROPOSED, never used until you accept them
research retrieval expansions RET_000001 --accept "earnings manipulation" --add "AAER" --reject "..." --rationale "..."
research retrieval search RET_000001            # R1 (+R2 with accepted terms) and R3a; hits with provenance, coverage, corpus snapshot
research retrieval show RET_000001
research retrieval review RET_000001 irrelevant --source SRC_X --reason "..."      # also: restore | add --source | acquire --title ...
research export retrieval RET_000001 --out DIR ; research export verify DIR
research retrieval evaluate --gold gold.json    # recall@k, precision@k, MRR of R1 and R1+R2 against gold labels
research corpus import-metadata --citation-key K --title "..." [--document-type working_paper --first-public-date 2025-03-10 --date-evidence "..."]
research corpus exclude SRC_X --reason "..." ; research corpus include SRC_X
```

## Stages

| Stage | What it does | Deterministic |
|---|---|---|
| R1 | BM25 over the stored sections of full-text sources and over the title and abstract of every source; query terms from the chosen readings (or the problem statement) and your own terms | yes (versioned tokenizer, fixed BM25 parameters, ties broken by id) |
| R2 | other names for the idea. A model's proposals are stored as PROPOSED and enter the search only after you accept them (or write terms yourself). A source found only through an accepted expansion is labelled R2 with the expansion that found it | the search is; the proposal is a recorded model output |
| R3a | held papers that cite, or are cited by, a paper on the list, from the stored reference lists (DOI, or a title of at least three words). Listed apart, with where it was found | yes |
| R5 | you set a hit aside (this run only), restore it, add a held paper you know, or record a paper you want to acquire (no source is created). Each needs a reason; each is recorded | — |

## Tiers

Only **FULL_TEXT** sources go on the reading list. **ABSTRACT_ONLY** and **METADATA_ONLY** sources are listed apart as *possibly relevant, not read*. They have no
stored text: no passage can be made from them, so they can never support or challenge a claim. `corpus import-metadata` records such a paper; it refuses to replace stored text.

## Working papers, preprints and versions

A Source can carry `document_type`, `review_status`, `first_public_date` (with its evidence: a date taken from the paper's own text is not accepted), `version_id`,
`work_id` and `version_relations`. Versions of one work are **one entry** in the reading list and one work in the coverage counts; the representative is the latest
by public date, and every version is listed with its type, review status and date. Nothing here treats a working paper as peer-reviewed or as evidence of a result;
that is a separate judgement (see `M5_WP14_PRECEDENCE_RULES_V2.md` §9).

## Corpus snapshots (invariant I20)

A search records the corpus snapshot it ran on and the members at that time. When the corpus changes (a source added, revised, excluded or included) the run is shown
`STALE_FOR_CORPUS` with what changed, until it is searched again. The old result stays as history. `corpus exclude` keeps the source and its history (a new revision with
your reason) and takes it out of the search.

## Coverage

Every run states: the snapshot; corpus size by tier, excluded sources and distinct works; what was retrieved (and that "searched corpus" means the retrieved sources, not
every source held); queries and expansions with their status; stage counts; exclusions with reasons; external calls (none); known gaps (not-read candidates, year range,
reference lists found or not, language not determined); the floor (`min_fulltext_sources`, default 3) and whether it is met after your review; and limits, including that
each query lists at most `top_n` (20) sources per tier.

## Evaluation

`retrieval evaluate` compares R1 and R1+R2 with gold labels fixed by a person or a fixture (no model grades anything) and reports recall@k, precision@k and the reciprocal
rank of the first relevant source, and where relevant-but-unreadable papers appear. `tests/retrieval_bench.py` is a synthetic corpus (invented text) with planted difficulties:
other names for one idea, decoys that share generic words, three versions of one work, an abstract-only and a metadata-only relevant paper, planted citations.
`tests/fixtures/retrieval_bench/baseline.json` records the figures; a change of method may not lower any of them and must report the new ones next to them.
These figures show mechanics, not performance on real literature.

## Limits

* The problem statement adds common words to a query: a statement-only query can rank decoys above relevant papers (the demo shows it). Use your own `--term`s or
  chosen readings; numbers on a real corpus are to be reported, not tuned on.
* Lexical only: no embeddings (an optional, benchmarked extra later). The light stemmer handles plurals only.
* R3a finds only citations between papers that are both held; it parses no bibliographic styles and looks nothing up.
* Language is not detected; library items (idea history) are not searched yet (WP10).
* Retrieval does not decide anything: no candidate, status or claim is produced here.
