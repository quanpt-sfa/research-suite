# Migrating from 9.1.1 to 9.2.0

9.2.0 changes what a workflow result *means*, not only what it computes. Read the first two sections before upgrading a project.

## 1. Four status axes replace the combined status

A 9.1.1 result carried one status string (`SUCCESS`, `REQUIRES_VERIFICATION`, ...). It mixed "did it run" with "is the conclusion
established". 9.2 records four independent axes (`workflows/status.py`, `docs/WORKFLOW_RUNS.md`):

| Axis | Values | Question |
|---|---|---|
| `execution` | SUCCEEDED, FAILED, NOT_RUN, BLOCKED | did the computation or model call happen? |
| `context` | COMPLETE, PARTIAL, INSUFFICIENT | was the evidence needed available and within budget? |
| `assessment` | NOT_ASSESSED, PROVISIONAL, VERIFIED_WITHIN_SCOPE, CONTESTED | how far is the conclusion established? |
| `applicability` | APPLICABLE, NOT_APPLICABLE, UNKNOWN | does this step apply to this study design? |

Nothing reaches `VERIFIED_WITHIN_SCOPE` from a run alone: it needs a researcher's confirmation, and a model-generated or high-impact
statement also needs a researcher-confirmed supporting adjudication. The old string is still produced (`legacy_status`) for one release;
a legacy `SUCCESS` maps to `assessment = NOT_ASSESSED`, never to a verified conclusion. Code that read `status == "SUCCESS"` as "established"
must read the `assessment` axis.

## 2. Legacy history

`research project upgrade-schema` (owner token) creates the run registry and imports each readable `workflow_cache/*.json` of 9.1.1 as a
**legacy** run: kept as history, `assessment = NOT_ASSESSED`, tagged with the defects the independent audit found in that workflow's 9.1.1
output (lexical verdicts, claims not scoped to the question, self-attested verification, ...), and never reusable. The files move unchanged to
`workflow_cache_legacy/`. A 9.2 run of the same workflow records the legacy run as its parent, so `research workflow diff` shows what changed.
Until a project is upgraded, `run_workflow` raises `SCHEMA_UPGRADE_REQUIRED`; the rest of the Research Core is unaffected.

## 3. What is new

| Area | What | Where |
|---|---|---|
| Model path | optional live adapter, scripted adapter for tests; the request is within the budget or it is not sent | `docs/MODEL_ADAPTERS.md`, `docs/CONTEXT_BUILDER.md` |
| Domain workflows | execution contracts (steps, entry conditions, loop-backs), not prompt fragments | `docs/DOMAIN_WORKFLOWS.md` |
| Corpus, reading protocols, ECA | stored papers, verified quotes, evidence-claim adjudication | `docs/KNOWLEDGE_LIBRARY.md` |
| Library | pins, dependency closure, lifecycle, promotion, journal policies, bundles | `docs/KNOWLEDGE_LIBRARY.md`, `docs/KNOWLEDGE_UPDATE_PROCEDURE.md` |
| Analysis pipeline | literature engine, introduction argument, playbooks, plans, execution DAG, claim change | `docs/ANALYSIS_PIPELINE.md` |
| Evidence of value | logic benchmark, Gate K and Gate L tooling | `docs/refactor_9_2/GATE_K_PROTOCOL.md`, `GATE_L_PROTOCOL.md` |

New object types (`DomainWorkflow`, `MethodPlaybook`, `JournalPolicy`, `KnowledgeItem`, `ContributionArgument`, `AnalysisPlan`) and the
`LIB_` id prefix (reserved: a proposal cannot create such ids). No new edge relation. Existing proposal schemas gained optional fields only.

## 4. Behaviour that changed on purpose

* A context that states nothing matches no validity assessment.
* An adjudication belongs to the exact words it read, not to a revision number.
* A hash-verified result is `VERIFIED_RUN` and still `PROVISIONAL`.
* A statistical diagnostic is never pass/fail and never blocks; only execution checks do.
* Knowledge is never `applicable` because it was used, or because the data have the right shape; it needs a validity assessment for the context.

## 5. Python and dependencies

Python 3.10-3.12; `jsonschema` is the only mandatory dependency. Optional extras: `model` (Anthropic SDK), `pdf` (`pypdf`), and `tiktoken` for exact local token counts.
