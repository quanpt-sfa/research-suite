# Gate L protocol: cumulative reuse on matched tasks

Gate L asks whether a library built on Study A makes Study B cheaper without making it worse. It is a counterfactual on the same
study: **Study B is run twice**, cold (empty library) and warm (the library after Study A, its adjudications and promotions).
Study C, in the same subfield but with an incompatible scope, measures false transfer.

## What you decide (the assistant cannot)

* **Study A**: the pilot (Zavitsanos et al. and `pu_finance`). L5 requires at least one real paper with its real code here.
* **Study B**: preferably real too: your own follow-up study, or a second real paper with code in the same subfield.
* **Study C**: a study whose scope is incompatible (another legal regime, label source or outcome measure), stated as a context.
* **The rubric, before any run**: gold passages for T1, gold extraction annotations for T2, the gold justified set for T3 (which playbook, with which
  status for Study B's context), and a tolerance for each of T1-T4. `gate_l.declare_rubric` stores it hashed and timestamped; `compare` refuses
  runs that started before it.

## Task families

| | Measures | Scored by |
|---|---|---|
| T1 | evidence retrieval: precision and recall of retrieved spans against gold spans | `score_retrieval` |
| T2 | extraction correctness, field by field, against the gold annotation | `score_extraction` |
| T3 | method selection: the top-ranked playbook and its status against the gold justified set | `score_method_selection` |
| T4 | fidelity: the share of critical elements scored MATCH or JUSTIFIED_ADAPTATION (Gate K's scoring) | `score_fidelity` |
| T5 | inference errors: overgeneralisations, dropped conditions, unsupported claims, from adjudication outcomes and your blind flags | `count_inference_errors` |

Effort is read from the run registry: model calls, commands, tokens (with their basis) and elapsed time. Manual steps are your own count and
are reported as such.

## What passes

* **L1** effort is lower on the task families where library items were reused.
* **L2** quality is no worse on every family T1-T4, within the tolerances declared in advance.
* **L3** T5 errors warm are not more than cold.
* **L4** Study C false transfer is zero, with every excluded item listed with its reason.
* **L5** Study A contains a real paper with real code (recorded as `real_evidence`; verified against the store).

## Procedure and artefacts

1. Declare the rubric. 2. Build Study A in the library (extract, adjudicate, validate, promote, retrospective). 3. Run Study B cold in a fresh project.
4. Run Study B warm in a fresh project that imports the library items (`research library use`). 5. Ask the library for Study C's context
(`gate_l.false_transfer`). 6. `gate_l.export_artifacts(..., real=True)` writes `docs/refactor_9_2/evaluation/gate_l/`; `research gate-validate L <dir>` is what the
acceptance test runs. `research benchmark gate-l-synthetic` shows the whole procedure on invented material; it can never pass Gate L (no L5).
Until the real directory exists the acceptance test skips and M4 is shown as not met.
