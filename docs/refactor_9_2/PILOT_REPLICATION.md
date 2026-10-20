# Pilot replication: Zavitsanos et al. (ICAIF 2025) and `pu_finance`

Decision D14 accepted this paper and its package as the pilot, on condition that the license, the usability of the data and
an exact reproduction of the model are checked at M3. This note records what was checked, how, and what is still open.
The material lives in `sample/` on the researcher's machine; it is git-ignored and never committed or pushed.

## License and entitlement

* The paper is CC BY 4.0 (stated in its text); the test fixtures contain only short excerpts, with attribution.
* The GitHub repository `iit-Demokritos/pu_finance` declares no license (API: `license: null`, no LICENSE file).
* The researcher states that a license exists. Its text was not shown to the assistant. The package is therefore recorded as
  `entitlement.status = confirmed`, with the basis "stated by the researcher" and that caveat in the record. The suite keeps the
  data locally only and never publishes it.
* The data carry Compustat-style variables (`gvkey`, `act`, `at`, ...). Whether the researcher's license covers redistribution of
  those variables is theirs to confirm; nothing here redistributes them.
* Mechanism: `add_replication_package(..., entitlement={status, license, basis})`. A package without a stated basis is
  `unspecified` and cannot be promoted to the library (`lifecycle.promotion_blockers`).

## Data usability

* 12 splits (test years 2003-2014), each with train and test CSVs; every split has unique (firm, report-year) rows and the
  fold structure is temporal (train = the three years before the test year). The training labels of unknown misstatements
  were flipped to negative by design (the package's README).
* The shipped results file does not use the CSVs as they are: `scripts/run_pu_trees.py` sets `drop_nan = True`, which drops
  rows with a missing metadata field. That explains why the results report 3001 test rows for 2003 while the CSV has 3192.
  The discrepancy is a documented preprocessing step, not an inconsistency of the data.

## Exact reproduction of the model

* The package pins Python 3.8, numpy 1.24.4, pandas 2.0.3, scikit-learn 1.0.2, scipy 1.10.1, Cython 3.0.12 and needs its
  Cython trees compiled. The Windows host has no C compiler, so the toolchain was built under WSL2 (Ubuntu 24.04, gcc 13.3):
  `scripts/replication/pu_finance_wsl.sh <package directory>`. It works on a copy, installs everything under `~/pu_repro`,
  and writes the rerun and a comparison next to the package (`sample/pu_finance/reproduced/`).
* The binaries that ship in the repository's `build/` directory are macOS builds; they were neither used nor executed.
* Result of the rerun of the PU-HRF experiment (`run_pu_trees.py`, seed 42): **every metric on all 12 splits is identical to
  the shipped results** (maximum absolute difference 0.0 for `average_precision`, `ndcg-precision`, `rec-precision`, `f1` and
  the train/test counts; only wall-clock time differs).
* Against the paper (Table 1, HRF PU, Financial + Metadata): AP 0.1281 (mean over the splits 0.128113) and R-precision 0.1822
  (mean 0.182278, which rounds to 0.1823: a last-digit difference of rounding or truncation, inside the declared tolerance of 1e-4).

## What this does and does not establish

* It establishes that the package's results are reproducible with its own pinned toolchain, and that the paper's headline
  numbers match them. That is `computational_reproducibility = REPRODUCED_WITHIN_TOLERANCE` for the elements linked to those outputs.
* It says nothing about methodological validity. Whether PU learning with this label construction suits another study is a
  validity assessment, per context, made by the researcher.
* The other models of Table 1 (Elkan-Noto, DevNet, CatBoost, ...) were not rerun.

Tests: `tests/test_replication.py::test_pilot_shipped_results_reproduce_the_numbers_reported_in_the_paper` and
`::test_pilot_model_rerun_matches_the_shipped_results_split_by_split` (both skipped when `sample/` or the rerun is absent).
