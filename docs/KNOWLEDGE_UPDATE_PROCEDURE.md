# Knowledge update procedure

How knowledge in the library changes over time without anyone being silently moved onto something new, and without a project ever
depending on the library to keep working. The mechanisms are in `docs/KNOWLEDGE_LIBRARY.md`; this is the order of work.

## Adding or changing an item

1. **Acquire** a candidate (`research library acquire --from source|run|policy|review|retrospective`). Nothing about it is checked yet.
2. **Formalize** it against the schema of its kind. Every element needs an evidence basis (the producer of a statement is not its evidence).
3. **Validate**: `research library validate ITEM [--context file]` computes source fidelity and computational reproducibility now, and records a
   validity assessment *for one stated context*. Confirming an assessment is the researcher's act (author or owner token).
4. **Adjudicate** every high-impact element (`research adjudicate run`, then `research adjudicate confirm`). `research adjudicate queue` lists what waits;
   `research adjudicate coverage` shows the 100% HIGH / stratified 20% STANDARD policy.
5. **Promote** (`research library promote ITEM --scope ... --exclusions ...`, owner token). Everything is recomputed at that moment. Without
   exclusions, say `--none-known`.
6. **Publish release notes**: `research library release-notes --pin PIN` states each dimension for each context, as recorded.

## Changing something already promoted

`research library revise ITEM --restrict | --extend | --supersede-by PIN | --record-failure`. Each creates a new revision; the old one stays and its pin still
resolves. A restriction adds exclusions; an extension adds scope but does **not** extend the promotion (re-promote with a new assessment); superseding makes
the old item no longer offered for new use; a recorded failure is shown at every later `library match`.

## What projects see

* A project that imported an item holds the whole closure and a manifest. Revising the library item changes nothing in the project.
* `research library check-updates --project P` lists newer revisions and restricted, superseded or withdrawn items. If the library is not there it
  says `LIBRARY_UNAVAILABLE`; that is a status, not an error.
* To take a new revision, import its pin again (`research library use`). The project then holds both revisions, and its history shows which was used when.
* `research library verify-closure --project P` checks the project against its manifests without opening the library.

## Moving knowledge between libraries

`research library export --pin PIN --out DIR` writes a bundle; `research library import --bundle DIR` verifies every file and every object digest and then writes
all of it in one transaction (or nothing). `--project P` imports a bundle into a project. Bundles carry full texts, so their circulation is bound by the
licenses of what is inside; the suite does not publish anything.

## Rules that do not bend

* Usage never raises validity. A project's use of an item is a usage record kept in the project.
* A model's reading is a proposal until the researcher confirms it; a `SUPPORTED_IF_QUALIFIED` verdict verifies nothing until the statement itself says what it qualifies.
* Knowledge outside its validated scope, or inside an exclusion, is not offered as applicable; it is shown as related, with the reason.
* Flawed methods are remembered: criticisms and failure records travel with the item.
