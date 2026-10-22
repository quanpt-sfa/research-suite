# Review protocol (WP6.1 remediation)

The method of a literature review is a method: how the literature was searched, what was screened in and out and why, whether and how studies were appraised, what was extracted and how it was synthesised. A critical or
systematic review needs no dataset and no estimator, and it can be checked. This record represents that method on the ResearchProblem (`review_protocol`) and checks it; it does not search a database and does not decide
what to include.

```bash
research review-protocol set PROB_000001 --protocol protocol.json --rationale "..." [--drafted-by assistant:claude]   # a DRAFT until the researcher adopts it
research review-protocol screen PROB_000001 --entries log.json --rationale "title and abstract, first pass"           # your decisions, stamped with who and when
research review-protocol adopt PROB_000001 --rationale "this is the plan I will follow"                               # author or owner; bound to the plan's digest
research review-protocol audit PROB_000001                                                                            # components, flow counts, the record against the corpus, readiness
research export review PROB_000001                                                                                    # search_protocol.md, screening_log.csv, corpus_inventory.csv + manifest
```

## The record

`review_type` (critical_systematic, systematic, narrative, scoping, conceptual); `streams` (id, name, question, terms: separate theoretical streams); `search` (`sources`: name, kind, `planned|executed`, queries, records
identified, date run; `date_from`, `date_to`, `languages`, `inclusion_scope`); `screening` (`procedure`, `criteria.include|exclude`, `stages`, `log`); `appraisal` (`performed` with approach and criteria, or `performed: false` with
`reason_if_not`); `extraction` (`fields`, reading `protocols`); `synthesis` (`method`, `how_tensions_are_judged`); `limitations`.

## Statuses of a component

`NOT_DECLARED` · `INCOMPLETE` (declared, something required is missing: the problems are listed) · `DRAFT_COMPLETE` (nothing is missing but the researcher has not adopted this plan) · `COMPLETE` (nothing missing and adopted) ·
`NOT_PERFORMED_JUSTIFIED` (appraisal only: not performed, with a reason, adopted).

**A plan drafted by an assistant is a draft until the researcher adopts exactly that plan.** Adoption is bound to the digest of the plan (everything except the screening log and the adoption fields): any edit voids it, while
appending screening decisions does not. The screening log is evidence of decisions; an exclusion needs its reason; the PRISMA flow counts are computed from it (`literature.prisma_counts`), from the records identified by the
executed search sources.

## The audit

`audit` compares the record with the corpus: which included records are held, and with a full text; which included records are not in the corpus; which sources the corpus holds that the log never mentions; the included
records per stream; and whether the review is ready to build a landscape (every component `COMPLETE` or `NOT_PERFORMED_JUSTIFIED`, the plan adopted, at least one included record, every included record held with a full
text). **It does not say that the search was adequate, the criteria sound or the review complete.**

## What uses it

The domain contract `LIB_DW_CRITICAL_SYNTHESIS` reads the component statuses: its method steps (search strategy, screening and selection, appraisal, extraction protocol, synthesis protocol) are `DECLARED_IN_RECORD` when the
adopted protocol's component is `COMPLETE`. See `docs/DOMAIN_WORKFLOWS.md`.
