# Gate K — Zavitsanos et al. (2025): reference-annotation review notes

**Status:** AI-prepared reference annotation, **not** a researcher-signed blind gold standard or an independently re-executed model replication. The seven original form elements were filled with summaries, exact short PDF excerpts, stable `SRC_ZAVITSANOS2025` identifiers, paper page locators, and code paths. Original uploaded form left unchanged.

**Sources and fixed versions**

- Paper: Elias Zavitsanos, Konstantinos Bougiatiotis, Andreas Sideras, and Georgios Paliouras (2025), *Positive-Unlabeled Learning for Financial Misstatement Detection under Realistic Constraints*, ICAIF 2025, DOI: 10.1145/3768292.3770366. User-uploaded 9-page PDF.
- Code/data: https://github.com/iit-Demokritos/pu_finance/tree/504a951d64df895137c4d4d5ecb57b50e98f82f5

## Seven critical elements recorded in the JSON

| Element | Grounded reference | Important limit |
|---|---|---|
| Label source/timing | Paper p.3 §3 and Fig.1: Audit Analytics restatement acknowledgment dates; delayed positives hidden in training; test labels are retrospective; repository `README.md`, `pu_finance/utils.py` | Dataset construction from raw Audit Analytics records is not provided in the reviewed code; zero/unlabeled does not mean verified non-misstatement. |
| Sample construction | Paper p.3: 47,086 firm-years, 2000–2014, ~1% positives; 12 yearly folds, train on three previous years; repository `data/split_YYYY/` | Full upstream universe/selection rules unreported; missing-data handling differs across scripts. |
| Prediction target | Paper §3 and p.5 §4: risk/rank of retrospectively known misstatement on test firm-year | Predictive model, not causal estimand; not prospective validation with final test labels available at t. |
| Specification/features | Paper pp.3–5: 28 base +14 derived financial features, metadata, nine algorithms; `utils.py` holds 42 numeric predictors | Paper names operating state, repo `METADATA_TO_USE` instead includes CIK; different scripts use differing metadata. Published CatBoost Focal Loss vs script Logloss. |
| Validation | Paper pp.3–5: rolling 2003–2014, 3 prior years per train, annual AP & R-precision, macro-averaged; `metrics.py` | Paper does not report formal sampling uncertainty, CIs or significance tests for model differences. |
| Diagnostics | Paper Fig.2–5, Tables 1–2: hidden positives, metadata, serial misstaters, CIK experiment, seen/unseen company F1 | Not every diagnostic has a corresponding runnable reproduction script in repo. |
| Interpretation limits | Paper pp.6–7: repeated firm identities inflate apparent accuracy, unseen-firm detection weak, update temporal domain and data needed | Do not interpret results as validated on new issuers or current deployment regimes. |

## Direct paper–code differences needing manual adjudication

1. **CatBoost objective:** paper p.4 §4 says CatBoost is equipped with **Focal Loss**; [`scripts/baseline_catboost.py`](https://github.com/iit-Demokritos/pu_finance/blob/504a951d64df895137c4d4d5ecb57b50e98f82f5/scripts/baseline_catboost.py) sets `loss = "Logloss"`, `scale_pos_weight = 2`. Do not infer which configuration generated the published Table 1 CatBoost line without run provenance.
2. **Missing-data rule and comparability:** [`scripts/run_pu_trees.py`](https://github.com/iit-Demokritos/pu_finance/blob/504a951d64df895137c4d4d5ecb57b50e98f82f5/scripts/run_pu_trees.py) sets `drop_nan=True` for its PU forest, while [`pu_finance/utils.py`](https://github.com/iit-Demokritos/pu_finance/blob/504a951d64df895137c4d4d5ecb57b50e98f82f5/pu_finance/utils.py) defaults to False and other models impute means. Effective samples may differ, and published comparisons must be checked against actual per-model counts.
3. **Metadata fields:** paper pp.3–5 refers to SIC, incorporation and operation states; repository declares CIK, SIC and `State of Inc`. The script for PU forest uses two metadata fields, excluding CIK; the PU-LR script includes CIK. Record actual specification per script, not a single assumed common feature set.
4. **Partial reproducibility:** existing 12 ready-made split CSV pairs and scripts support experiment replay in principle, but the original raw-label construction and source-vintage transformations are not included. Some compiled PU-tree components target older platforms/Python. No actual model rerun or Table 1 replication was performed in this annotation task.
5. **Output labeling:** the forest-running script instantiates `PURandomForestClassifier` but prints `PU-HDT Average scores`; do not use that print label to identify the estimator.

## Independent arithmetic cross-check (not a model replication)

The released [`results/PUHRF_baseline_meta_noCIK.csv`](https://github.com/iit-Demokritos/pu_finance/blob/504a951d64df895137c4d4d5ecb57b50e98f82f5/results/PUHRF_baseline_meta_noCIK.csv) contains 12 test-year rows. Direct arithmetic gives:

- Unweighted mean `average_precision` = **0.1281130588**, consistent with Table 1 Financial+Metadata HRF AP **0.1281**.
- Unweighted mean `rec-precision` = **0.1822775419**, close to Table 1 Financial+Metadata HRF R-precision **0.1822** (difference ≈ 0.000078; standard four-decimal rounding of the CSV would be 0.1823).

This only verifies consistency of **a published output CSV with an aggregate table**, not that the prediction scores, model, training labels or sample were independently recomputed.

## What is required before Gate K is passed

1. A qualified human annotator must read and confirm each of the seven elements **without having seen the system output to be scored**. If an independent blind assessment has not been preserved, label the resulting review as non-blind rather than backdating it.
2. The human must adjudicate the paper–code differences above and preserve qualified judgments with exact locators.
3. Codex should run the relevant model scripts on the supplied split files using a documented compatible environment, hash data/code/outputs, and reproduce at least the selected Table 1 row and diagnostic within a declared tolerance. Record mismatches separately from the scientific validity assessment.
4. Execute the Research Suite playbook only after the gold/reference annotation is frozen and recorded; score MATCH/JUSTIFIED_ADAPTATION/PARTIAL/MISMATCH element by element.

**Do not mark Gate K PASS on the basis of this AI-drafted form alone.**
