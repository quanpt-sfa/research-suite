# From literature to claim change (9.2, milestone M3)

Plan: `docs/refactor_9_2/RESEARCH_SUITE_9_2_REFACTOR_PLAN.md` (3B, 3C, 3D, 3E, 5a, 5b). Builds on `docs/KNOWLEDGE_LIBRARY.md`.
Acceptance: `docs/refactor_9_2/milestone_report.md`. End-to-end proof: `tests/test_pilot_chain.py`.

| Stage | Module | Command |
|---|---|---|
| 3C literature engine | `knowledge/literature.py` | `research literature run --purpose theory\|method\|contribution --sources A,B` |
| 3B introduction argument | `knowledge/argument.py` | `research intro analyze --source S` |
| 3D playbooks, selection, plans | `knowledge/playbooks.py`, `journal_policy.alignment` | `research playbook draft\|select`, `research plan draft\|fidelity` |
| 3E execution DAG | `execution/dag.py`, `execution/diagnostics.py` | `research plan run PLAN --workdir DIR` |
| 5a claim change, promise check | `knowledge/loop.py` | `research claim-change assess\|confirm` |
| 5b replication | `knowledge/replication.py` | `research replication add\|reproduce` |
| D15 adjudication coverage | `workflows/eca.py` | `research adjudicate coverage` |

## What each stage will and will not claim

* **Literature engine.** One extraction per paper, cached by (source revision, protocol version), served from an imported library at
  no model cost. It reports structure: which sources define a construct in different words (a flag), which assumptions are stated and
  not recorded as tested, which sources report which method field. A `MethodRecord` is `QUOTE_VERIFIED` at most. Systematic mode needs
  a search protocol and a screening log and computes the PRISMA 2020 *flow counts* (the 27-item checklist is not exported).
* **Introduction argument.** Five components, each element a statement with its producer apart from its evidence. What authors assert is
  `AUTHOR_ASSERTED`; `CORROBORATED` needs a researcher-confirmed adjudication against a passage from another source. The contribution
  comparison consumes the contribution review of 3C: it cites only passages of that review that still verify, and its entries stay
  `PROVISIONAL` until the researcher gives their own judgement.
* **Playbooks.** Elements carry their own fidelity, reproducibility and validity assessments. A playbook that was extracted and
  reproduces is a *candidate, validity not established here* until an assessment exists for the project's context. Selection also
  evaluates the playbook's conditions (SATISFIED / NOT_SATISFIED / UNKNOWN with the fact needed) and never recommends a method from the
  shape of the data. A context that states nothing matches no assessment. An analysis plan is a *proposal*; a playbook that does not
  apply to the study design is refused, so a qualitative study is not given an econometric plan.
* **Execution DAG.** Only execution checks are pass/fail and only they block (`BLOCKED, upstream_failed`). Diagnostics record the
  values, every criterion with its source, and a signal; criteria that disagree give `INCONCLUSIVE` with all shown; a concern stops
  nothing and a lack of concern is not a pass. Signals reach the domain workflow, whose `on_condition` rules can send the researcher
  back to an earlier step. Robustness divergences are evidence, judged only against a tolerance the plan declared with its basis.
  Non-significance readings (`UNDERPOWERED`, `IMPRECISE`, `NEGLIGIBLE_EFFECT_SUPPORTED`, `MEASUREMENT_CONCERN`) need a declared
  smallest effect of interest and alpha; with either missing there is no reading. A hash-verified result is `VERIFIED_RUN` and still
  `PROVISIONAL`. Running a plan executes its commands on this machine, never through a shell, and needs the owner's token.
* **Claim change.** Derived from claim effects and divergences, never from a raw signal or from non-significance. `NOT_SUPPORTED`
  needs a recorded structured contradiction; `REVERSED` needs structured evidence. Every change is HIGH risk and `PROVISIONAL` until
  the researcher confirms it. The promise check yields manuscript revision *suggestions* with their basis; none is applied.
* **Coverage (D15).** Every HIGH statement is to be adjudicated, plus a deterministic stratified sample of STANDARD ones (20%, at
  least one per stratum), doubling per problem found in a stratum. Whatever a material conclusion rests on is made HIGH.

## Limits

* All model-backed steps were exercised against scripted adapters; no live model call has been made.
* Claim effects are entered by the researcher (or a model's proposal the researcher reviews); there is no automatic drafting of them.
* For result-based statements (a claim change) the confirmation is the researcher's; ECA compares a statement with a *source passage*.
* The Gate K blind-annotation forms and the Gate L cold/warm comparison belong to M4.

## Keeping the confirmation workload proportionate

A first real run on the pilot paper (DeepSeek, `RP_METHOD@1`) produced a playbook of 47 elements, all high-impact, each needing a confirmed adjudication. What changed:

* **Caps per field** (protocol data, `max_records`): the model is told how many records a field may have and to put the most decision-relevant first; extra records are rejected as `over_cap` and listed. The caps are a judgement; raise them for a paper that compares many methods.
* **Decisive versus supporting.** A field marked `decisive: false` (study design) yields *supporting* elements: quote-verified, STANDARD risk, sampled for adjudication under the 20% policy, not critical, and not required of a plan or of promotion.
* **One element per passage.** Records that point at the same words (whatever field they came under) are one element (`also_extracted_as`).
* **A value restates only its own quote** (protocol instruction); the quote check cannot see a value that adds details, but the adjudication does.
* **Batch tools.** `research adjudicate run-pending` runs the adjudications the coverage policy still asks for (proposals only); `research adjudicate sheet --out sheet.json` writes every high-impact proposal with the statement, passage, the model's reading, the flagged cues and any guard, items needing attention first and every decision left empty; `research adjudicate confirm-batch --file decisions.json` applies your decisions, each with its own verdict and rationale. There is no default verdict and no "agree with all".

Measured on the same paper with `RP_METHOD@4`: 23 elements (21 decisive, 2 supporting) against 47; 22 adjudications, 16 supported as stated, 6 flagged (statements that add something to their quote, such as a name for what the quote calls "positives"). Those are for you to decide; they are what the check is for.
