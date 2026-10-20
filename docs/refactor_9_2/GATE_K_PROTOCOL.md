# Gate K protocol: method fidelity on the real pilot

Gate K asks whether the method the system reconstructs from a paper is the method the paper used. It is judged against a reference
that the researcher writes **before** seeing anything the system produced. The assistant prepares the form and the tooling. It does
not fill any reference answer and does not score anything.

Pilot: Zavitsanos et al., ICAIF 2025, and the `pu_finance` package (see `PILOT_REPLICATION.md`).

## What is already prepared

* `docs/refactor_9_2/evaluation/forms/gate_k_annotation_form_pilot.json`: the blank form (seven critical elements, empty answers).
* The tooling: `research gate-k form | annotate | blindness | sheet | score | evaluate | export`, and `research gate-validate K DIR`.
* The rules the tooling enforces: an annotation stored after any system output about the same sources is *not blind* and scoring refuses it;
  an annotation is never rewritten; a `JUSTIFIED_ADAPTATION` needs an adapted plan step with its rationale; a `MISMATCH` counts as
  flagged only if the plan records an adaptation or omission for it; the system cannot be scored `MATCH` on an element it does not have, or
  on one the paper does not state.

## Steps (yours)

1. **Create the project and import the paper.** Importing stores text; it produces no system output about the method.
   ```
   research project init PRJ_PILOT_REAL --owner researcher        # keep the token it prints
   research corpus import --project PRJ_PILOT_REAL --file sample/zavitsanos2025positive-unlabeled.pdf --citation-key zavitsanos2025 --token-file <token file>
   research replication add --project PRJ_PILOT_REAL --id PKG_PUHRF ...   # optional now; it is not system output about the method
   ```
   **Do not** run `research extract`, `research literature`, `research playbook` or `research plan` on this paper before step 3.
2. **Fill the form** from the paper and the package: for each element, what the authors did (your words), the supporting quote and `source_id`
   (`SRC_ZAVITSANOS2025`), or `not_stated` with a reason. A second reader may be named (`second_reader`).
3. **Store it**: `research gate-k annotate --project PRJ_PILOT_REAL --file <filled form> --token-file <token file>`. Check
   `research gate-k blindness REFANN_PILOT --project PRJ_PILOT_REAL`: it must say *blind*.
4. **Let the system run**: literature (method) review, playbook draft, your ECA confirmations of its HIGH elements, a validity assessment for
   the pilot context with a methodological basis, an analysis plan, the replication reproduction (`research replication reproduce`).
5. **Compare**: `research gate-k sheet --annotation REFANN_PILOT --playbook <PB> --plan <PLAN> --out sheet.json` writes reference and system side by
   side with empty `score` and `note` fields.
6. **Score each element yourself**: `MATCH`, `JUSTIFIED_ADAPTATION`, `PARTIAL` or `MISMATCH`, with a note for the last two. Store with
   `research gate-k score ... --file scores.json --scorer <your name>`.
7. **Evaluate and export**: `research gate-k evaluate --scores GATEK_SCORES_PILOT --package PKG_PUHRF --real` then
   `research gate-k export --scores GATEK_SCORES_PILOT --package PKG_PUHRF --real --out docs/refactor_9_2/evaluation/gate_k`.
   `research gate-validate K docs/refactor_9_2/evaluation/gate_k` is what the acceptance test runs.

## What passes

* **K1** the fidelity report is complete: every plan step has a fidelity and every critical playbook element is addressed.
* **K2** every element is `MATCH` or `JUSTIFIED_ADAPTATION`, no `MISMATCH` is unflagged, and the annotation was blind.
* **K3** the key outputs reproduce from the replication package within the declared tolerance.
* **K4** no validity assessment is `SUPPORTED_WITHIN_SCOPE` without a basis from methodological literature; open known criticisms are listed.

Whether to commit the exported artefacts is your decision: they contain your annotation of a CC BY paper (short quotes). Until the
directory exists the acceptance test skips, and the milestone report shows M4 as not met.
