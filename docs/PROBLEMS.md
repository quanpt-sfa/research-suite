# Research problems (9.2, M5 WP4)

A `ResearchProblem` is a phenomenon, puzzle, disagreement, unexpected result, practical problem or method gap that is **not yet a question**. It is a separate
object type (decision D16): a `ResearchQuestion` is answerable and the stage gates of a domain workflow open on one, whereas a problem can have several readings
and no answerable form. It needs no corpus, no Introduction and no question. Design: `docs/refactor_9_2/M5_TASKS_AND_DATA_MODEL.md` §2.3.
A demonstration with its output: `docs/refactor_9_2/demos/wp4_problem_demo_output.txt` (`python scripts/demos/wp4_problem_demo.py`).

```
research problem new --statement "..." --kind phenomenon --scope '{"domain": "accounting"}' --interpretation "a reading in your words"
research problem frame PROB_000001            # a model proposes further readings: stored PROPOSED, never chosen
research problem add-interpretation PROB_000001 --text "..."
research problem choose PROB_000001 --interpretations I1,I3 --rationale "..."      # yours; the reason is required
research problem promote-to-rq PROB_000001 --interpretation I1 --text "the question, in your words" --rationale "..."
research problem revise PROB_000001 --rationale "..." --statement "..."            # a new revision; promoted questions are flagged for review
research problem set-aside | reopen | show | history
```

## What is recorded

* Interpretations carry their author (`researcher` or `model:<id>`), a status (`STATED`, `PROPOSED`, `CHOSEN`, `NOT_CHOSEN`) and, for proposals, constructs,
  unit of analysis, the kind of answer it would admit and the terms a search would need. A model proposal never changes the statement or the scope, is never
  chosen by being proposed, and is registered as a run (`problem_framing`: model, prompt digest, usage); a failed or unusable answer is a failed run and
  stores nothing.
* Every change is a new revision written against the revision the writer read (`STALE_REVISION` otherwise); `decisions` is the readable log (who, when,
  what, why), the revisions are the full history. Choosing, reframing, setting aside, reopening and promoting need an author or owner token and a reason.
* A **new statement** withdraws the choice of interpretations (they were readings of the old statement); a change of scope or exclusions keeps it.
* **Promotion** makes a `ResearchQuestion` whose text you write (it is not copied from the interpretation), only from an interpretation you chose. One
  transaction writes the question and the problem revision that records the promotion; the question has a `derived_from` edge (`edge_kind: promoted_from`) to
  that revision. When the problem is reframed later, the question is flagged `NEEDS_REVIEW` and `problem show` reports that its pinned problem revision is
  no longer the current one.
* A task can address a problem: `research task start --problem PROB_000001`.

## Limits

* The suite does not judge a reading: `answer_kind` and `search_terms` are a model's labels for the researcher to read, not findings.
* Promotion creates one question per call; several questions from one problem are allowed (`promoted_to` is a list). The first draft of the data model said
  a single id.
* `discover_contribution` still needs retrieval, landscape, candidates and verification (WP5–WP8); the problem is its entry point, not the workflow.
