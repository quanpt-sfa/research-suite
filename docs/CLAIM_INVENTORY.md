# Claim inventory (M5 WP6.2)

A claim inventory lists the claims of a set of papers: **who holds each claim**, what kind of claim it is, the authors' own words for it, what else in the paper it rests on, and what you decided
about it. It also holds the **relations** between claims and the **alignments** of a claim across the versions of one work. Every relation and alignment is a proposal until you decide.

Nothing here says a claim is true, established or new. A claim count is not evidence that a paper was read completely.

## What it is for, and what it is not

| It is | It is not |
|---|---|
| a per-paper reading that any problem, landscape or draft can reuse | a verdict on any claim |
| a place where "Smith (2020) argues X" and "we find X" are different things | a claim that the cited study says X: the suite has not read it |
| a record of several passages for one claim, each with its reason | a knowledge graph database, or an ORKG or nanopublication client |
| a set of proposals with who proposed them and what is missing | an automatic merger of claims of different authors |

## The two axes

**Attribution**: who holds the claim.

* `AUTHOR_OWN_CLAIM`: the authors assert it as their own result or argument.
* `AUTHOR_REPORTS_PRIOR_WORK`: the authors describe what earlier work found or argued. It stays `REPORTED_BY_CITING_AUTHOR`.
* `AUTHOR_CRITIQUES_PRIOR_WORK`: the authors dispute or qualify earlier work.
* `RESEARCHER_INTERPRETATION` and `MODEL_PROPOSED_INTERPRETATION`: not the authors' at all. They have no wording, are never `author_stated`, and a model's must say what is missing.

Extraction can assign only the first three. A record with no attribution is kept as `NOT_ASSIGNED`, never defaulted to the authors.

**Role**: what kind of claim it is. `EMPIRICAL_FINDING`, `INTERPRETIVE_FINDING`, `THEORETICAL_PROPOSITION`, `METHODOLOGICAL_CLAIM`, `DEFINITION`, `CLAIMED_CONTRIBUTION`, `ASSUMPTION`, `BOUNDARY_CONDITION`,
`LIMITATION`, and `GAP_STATEMENT` (the authors identify a gap; added because the specification lists it among the cases to keep apart).

An attribution is never a role: the same role (a proposition) can be the authors' or a report of earlier work.

### Citation cues are flags

Fixed patterns find citation cues ("Alder (2018) argues", "(Brandt, 2016)", "[3]", "previous studies have shown") and own-work cues ("we find", "this paper") in the claim's wording. When the model's
attribution disagrees with them, `research claims audit` lists the claim. **A flag is a reason to look; it never changes the attribution.**

### Reported work is not checked work

A claim that reports earlier work lists the citations as the authors give them (`cited_works`, state `REPORTED_BY_CITING_AUTHOR`). `research claims link-cited` records which held Source a citation refers to
(`CITED_SOURCE_LINKED`), and that is all: the cited work counts as **checked** only after you have confirmed a relation between the claim and a claim read from that very Source.

## What a claim holds

* **wording**: a verified span of the stored text at Source@revision (character offsets, section). The only part that is the authors' for certain. A claim from extraction cannot exist without it.
* **text**: the model's restatement, kept apart. ECA judges it; until you confirm it the claim is `AUTHOR_REPORTED`.
* **granularity**: `ATOMIC`, or `COMPOUND` (then `components`, each with its own conditions; the whole stays one claim with its qualifiers).
* **attributes**: constructs, scope (population, setting, period, level of analysis), premises, mechanism, boundary conditions, direction. Each carries a basis: `STATED_IN_QUOTE` if all its content words
  (numbers included) are in the claim's quotations, otherwise `MODEL_INFERRED`. **This is a textual check, not a reading of meaning**: a paraphrase reads as inferred; words that are all present but combined
  in a way the text does not state read as stated. Absent attributes are absent, never defaulted.
* **evidence links**: the `WORDING` link, and any number of further verified spans with a role (`QUALIFIER`, `SUPPORTING_EVIDENCE`, `PREMISE`, `CONTEXT`, `BOUNDARY`), the reason each is needed and where it came from
  (`extraction`, `followup`, `researcher`).
* **reviews**: your decisions (`CONFIRMED`, `CORRECTED` with the corrected fields, `REJECTED`); a correction keeps the model's own value beside yours.

## Relations and alignments

Eleven relation kinds: `SUPPORTS`, `CHALLENGES`, `QUALIFIES`, `DEFINES`, `ASSUMES`, `DEPENDS_ON`, `EXTENDS`, `RECONCEPTUALIZES`, `CONTRADICTS_WITHIN_SCOPE`, `COMPLEMENTS`, `SAME_IDEA_PROPOSED`. They go through a
deterministic gate (`knowledge/claim_relations.py`): `DEFINES` starts at a `DEFINITION`; `ASSUMES` ends at an `ASSUMPTION`; `CONTRADICTS_WITHIN_SCOPE` needs the shared scope and refuses claims whose scopes
differ (a model's also needs the level of analysis of both); `SAME_IDEA_PROPOSED` needs two works and a comparison of **meaning**, never of wording; a model's proposal needs a rationale and what is missing; a
rejected claim cannot be related. A relation is stored with Source@revision and passage@revision of each end, the rationale, scope compatibility, who proposed it, `status: PROPOSED` and your reviews.
It is `needs_reassessment` when a Source it rests on was revised or you corrected a claim after the proposal.

Alignments relate a claim of an earlier version of a work to one of a later version: `UNCHANGED`, `QUALIFIED`, `NARROWED`, `WEAKENED`, `STRENGTHENED`, `REVERSED`, or, resting on an absence, `WITHDRAWN` and
`NEW_IN_LATER_VERSION` (these name the other version that was searched, carry its coverage caveat, and require you to say what is missing). The earlier version must be earlier by its public date, or you say
which is. One standing proposal per pair of claims. Claims are never copied from one version to another.

Nothing is derived from similarity. `research claims candidates` and `align-candidates` list claims that share words, and write nothing.

## Reading cost

`RP_CLAIMS@1` reads every non-reference section once and has **no record cap**. Its cache key includes the model identity and the settings (chunk size, spans, extra context), so another model or other
settings is another run, never served another run's records. The older protocols keep their keys; a reuse of one of them now says whether the cached model is the one asked for (`reuse.model_matches`).

`research claims followup` makes one `RP_CLAIM_EVIDENCE` call for a claim only if the model asked for deeper reading or the claim is compound, over a few sentences chosen by words alone (at most about 2,500
characters), with `--max-claims` as a budget (the claims left out are listed). A finding with nothing beyond its wording is **flagged** in the report and costs no call unless you ask with `--reason`.

`claim_views.derive` gives the records an older protocol would hold (landscape entries, introduction-argument components, theory, contribution, method) from the stored claims, with no call, and lists the fields
no claim can fill (`NOT_DERIVABLE`, or `PARTIAL` when a claim is only a candidate). It separates the authors' own findings from reports of earlier work, which `RP_LANDSCAPE` does not.

Measured offline on invented papers with a rule-based stand-in (not a model; tokens are estimates, never billing): `docs/refactor_9_2/evaluation/claims/strategy_cost_comparison.{json,csv}` and the report.

## Exports

`research export claims INV` writes `claim_inventory.csv`, `claim_evidence_links.csv`, `claim_argument_relations.csv`, `claim_version_history.csv`, `claim_coverage_report.md`, `claim_attribution_audit.md`,
`claim_graph.md` (Mermaid, dashed for proposals), `claim_benchmark_results.json`, `token_cost_report.csv` and `manifest.json`, with a closure digest; `research export verify DIR` tells intact, modified and
out-of-date files apart. Quotations are at most 300 characters. Provenance classes: `data_derived`, `model_synthesised`, `researcher_confirmed`, and in relation and alignment tables also
`researcher_proposed_unconfirmed` (your own proposal, not yet decided).

## Benchmarks

`research_harness/benchmarks/claims.py` computes claim precision/recall/F1, attribution and role accuracy with confusion matrices, evidence span precision/recall and overlap, claim-evidence link
precision/recall, qualifier omission, multi-span coverage, relation precision/recall, version-alignment error, unsupported-claim rate and cost, with **exact**, **overlap** and **lexical** matching reported
separately (lexical is word overlap, not semantic matching). It computes no composite score.

**Gold.** It accepts gold only if it declares `annotation.independent_of_system_output = true`, names human annotators, and carries no `generated_by: model:`. `research claims annotation-form` writes a blank
form for a person to fill before seeing any system output; the suite never fills it. Two annotators' disagreements are kept (`compare_annotators`) and an adjudicator's reasoned decision resolves each
(`adjudicate`); an undecided one is left out and counted.

**What data exists.** Public: SciFact (claims CC BY 4.0, abstracts ODC-By 1.0) for evidence-sentence and abstract selection only. SciArg could not be fetched and its licence is unclear; CLAIM-BENCH has no
licence file (and ships 89 published PDFs); neither is used. See `docs/refactor_9_2/M5_WP6_2_PRIOR_ART_AUDIT.md`. Results on the invented fixture gold test the metric code and are labelled
`SYNTHETIC_FIXTURE`: they say nothing about any model.

## Commands

```
research claims build SRC... [--strategy single_pass|section_aware] --model M     # model calls; scripted in tests
research claims show INV | list | audit INV | candidates INV CLAIM
research claims review INV CLAIM CONFIRMED|CORRECTED|REJECTED --reason R [--set attribution=...|role=...|text=...]
research claims link-cited INV CLAIM --citation "Alder (2018)" --source SRC_...
research claims interpret INV --text T --about C001 --rationale R
research claims add-evidence INV CLAIM --quote Q --role QUALIFIER --why W
research claims followup INV [--max-claims N] [--reason NO_EVIDENCE_BEYOND_WORDING]
research claims relation-add|relation-propose|relation-review ...        research claims align-add|align-propose|align-review|align-candidates ...
research claims annotation-form SRC --annotator A --out form.json         research claims benchmark INV --gold g.json [--cross c.json] [--record]
research export claims INV --out DIR                                      research export verify DIR
```

Live model runs are paid; the backlog item on partial provider usage (B1) is open, so none was run for WP6.2.
