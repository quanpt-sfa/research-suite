# Context builder (9.2, Phase 2)

`build_context(store, workflow, ...)` assembles what a workflow reads. Its job is to make the request **complete
for the question being asked and no larger than the budget**, and to say plainly when it cannot be both.

## Scope

* **Research question.** Explicit id, or the only one in the project. Several questions and no choice raises
  `AmbiguousResearchQuestionError`. Unknown ids raise `KeyError` (`RESEARCH_QUESTION_NOT_FOUND`, `CLAIM_NOT_FOUND`).
* **Claims in scope.** Explicit `focus_claims`; otherwise the claims connected to the question directly or through
  a `Result` / `Analysis` / `Claim` chain (at most two hops). A source, theory or construct shared with another
  question is never a bridge, so another question's claims do not leak in. `ContextPackage.scope_claims` lists them.
* **Expansion.** One hop from the question and the claims, by the workflow's table (`_EXPANSION` in `context.py`).
  Contribution also reads the question's `Source`s and their passages (competing papers); passages derived from an
  included source are always read with it.
* **Relations.** Every edge between included objects is in `ContextPackage.relations`. `supports` / `challenges`
  direction is a convention (nothing enforces it), so a link in either direction between a claim and an object counts.
* **No wording-based flags.** Evidence is "challenge-linked" only through a `challenges` edge or an explicit structured
  field (`contradictory: true`, `assessment.relation == "challenges"`).

## Budget (the whole request)

`max_input_tokens` covers the system prompt (skill + output contract), the header and the evidence. Material is
admitted by tier until the budget is spent:

| Tier | Contents |
|---|---|
| T0 must-have | the question, the claims under assessment, everything they link to by `supports` / `challenges` / `depends_on` / `tests` / `estimated_with` / `derived_from`, challenge-linked evidence, adjudications, the sources of must-have passages |
| T1 relevant | the workflow's primary object types (`_TIER1_TYPES`) |
| T2 background | everything else that was expanded |

After selection the request is measured and trimmed (loaded full texts become paged first, then the lowest tiers
are dropped) until it fits. Each omission is recorded with its tier and reason.

| `coverage` | `context_status` | Meaning |
|---|---|---|
| `FULL` | `COMPLETE` | nothing left out |
| `PARTIAL_T2` | `PARTIAL` | only background left out |
| `PARTIAL_T1` | `PARTIAL` | relevant evidence left out, or a requested full text was deferred |
| `INSUFFICIENT` | `INSUFFICIENT` | a must-have item does not fit, or the request cannot be made to fit |

A model-backed run refuses `INSUFFICIENT` always and `PARTIAL_T1` unless `allow_incomplete=True`.

## Full text

Statuses: `FULLTEXT_LOADED`, `FULLTEXT_AVAILABLE` (exists, not requested), `FULLTEXT_DEFERRED` (requested, does not
fit), `ABSTRACT_ONLY`, `FULLTEXT_MISSING`, `RETRIEVAL_FAILED` (artifact referenced but unreadable). An abstract is never
presented as full text. A deferred source stays in the context with its abstract, size, sha256 and detected sections;
read it with `get_fulltext_page(store, source_id, offset=..., limit=...)` (`next_offset` is `None` at the end).

## Section-aligned comparison

`build_context(..., comparison_dimension="sample_construction")` (or `compare_sources(store, ids, dimension)`) reads the
matching section of each source: its own share of the budget, every block with a locator `SRC@rev[start:end]`.
Truncated blocks carry `next_offset`; sections not reached are listed in `unread_sections`; sources without full text
or without a matching section are reported as such. Sections are found structurally (`sections.py`, vocabulary in
`taxonomy/sections.json`); the detection never feeds a verdict.
