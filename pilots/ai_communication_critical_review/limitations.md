<!-- origin: ASSISTANT_AUTHORED text, written into the repository by the pilot script -->
# Limitations and status

**Status: REAL_CORPUS_VALIDATION_PENDING.** This pilot is a reproducible setup and a readiness report. It is not a validation of the suite on real literature.

## What exists

* An isolated store (`PRJ_AICOMM_PILOT`) with the ResearchProblem and its inquiry, a DRAFT review protocol drafted by the assistant, and 26 metadata-only Sources whose metadata matched a Crossref record.
* An internal retrieval run over that metadata: 0 full texts on the reading list, 19 possibly relevant papers listed as not read; below the floor of full texts, as it must be.
* A review audit: protocol DRAFT; ready to build a landscape: False.

## What does not exist, and why

* No full text of any included study is held in the project (every Source is METADATA_ONLY). The researcher must supply lawfully accessible full texts.
* No approved model run: the landscape needs the reading protocols run by a model, which sends text to a provider; this needs the researcher's permission per source, a budget the suite can account for, and a rotated API key.
* Backlog B1 (partial provider usage) is still open: a live run could not be metered reliably enough for an uncontrolled batch.
* The researcher has not adopted the review protocol or screened any record.

## What the assistant did and did not do

* The seed list is the assistant's own selection from its knowledge, not a search result. Its titles, authors and years were checked against Crossref by `scripts/pilot/verify_seeds.py`; 1 seed(s) that did not match (simon1971; see the audit) were left out of the corpus.
* The review protocol, the draft inclusion and exclusion criteria, the planned database queries and the stream questions are the assistant's drafts. They count as the researcher's only after `review-protocol adopt`. The appraisal component is deliberately not declared.
* No screening decision was made on anyone's behalf. No DOI was typed from memory: every DOI in this directory was returned by Crossref for a matching record. No quotation of an author appears anywhere.
* No model was called. No full text was downloaded. Nothing was sent to a third party except bibliographic lookups to the public Crossref API (titles, first authors, years).

## Methodological decisions that belong to the Research Owner

1. Whether and how studies are appraised in this review (the appraisal component is undeclared).
2. The inclusion and exclusion criteria, the screening procedure and who screens.
3. Which databases are searched (the planned queries are drafts), and whether the seed list is a starting point or is discarded.
4. Which seeds are screened in; in particular, for seeds whose Crossref record is a preprint, whether to find and cite the published version.
5. Whether full texts may be sent to a model provider, for which sources, with which budget; and whether to rotate the key and close backlog B1 before a live run.
6. Whether the three execution contracts describe how this kind of review is actually done (they are `EXTRACTED` skeletons).

## How to reproduce

```
python scripts/pilot/verify_seeds.py --seeds pilots/ai_communication_critical_review/seeds.json --out pilots/ai_communication_critical_review/seeds_verified.json   # needs the network
python scripts/pilot/build_ai_communication_pilot.py                                                                                         # offline, from seeds_verified.json
```
