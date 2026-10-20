"""Benchmarks of the 9.2 refactor (plan v3, phase 6).

* `logic`: the synthetic logic benchmark (6.1). It checks that the software follows its designed logic. It is not evidence
  of scientific usefulness.
* `gate_k`: tooling for the method-fidelity evaluation (6.2): a blank annotation form, a blindness check, comparison sheets,
  scoring and the Gate K report. The reference annotation and the scores are the researcher's; nothing here writes them.
* `gate_l`: the cumulative-reuse evaluation (6.3): declared rubrics, task-family scores, effort measured from the run
  registry, and the cold/warm/contrast comparison. A synthetic version runs in CI; the real one is recorded as artefacts.

Every number a benchmark reports is computed from what ran. None is typed in.
"""
