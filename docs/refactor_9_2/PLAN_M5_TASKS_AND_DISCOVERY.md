# Research Suite 9.2 → M5: task entry points, Contribution Discovery, knowledge accumulation, readable outputs

**Status: plan for the owner's approval. Nothing in this plan is implemented.** Wherever a sentence describes existing behaviour it cites a file,
function, test or command; everything else is a proposal. Baseline: `main` = `refactor/9.2` = `c6397de` (2026-10-21).

**Design reviews.** First review (2026-10-21) → `M5_DESIGN_AMENDMENT_1.md`. Second review (2026-10-22): Amendment 1 accepted in direction; WP0
approved; WP1–WP2 approved on condition that the transaction and export contracts are closed before code and that `GATEL_REAL` and every store under
`sample/` stay untouched; WP14 approved for drafting and testing only; WP3–WP13 not approved. Response: `M5_DESIGN_AMENDMENT_2.md` and the
self-contained `M5_WP14_TRUTH_TABLE_DRAFT.md`. Precedence: Amendment 2 > Amendment 1 > this plan and the supporting documents.
Third review (2026-10-22): WP0 start; WP1 (transaction integrity, CAS, O17) and WP2 implement with tests; WP14 complete design and expert cases;
WP3–WP6 open by dependency after WP1; WP7–WP13 not approved for production. Response: `M5_WP14_PRECEDENCE_RULES_V2.md` (+ `wp14/fixtures_v2.json`,
`wp14/expert_cases_form.json`) and `M5_WP1_WP2_CONTRACTS.md`. **Status:** WP0 done (`abe1578`, CI run 39656916379 green); WP1a done (`f529989`, CI run 39658649667 green); WP1b deferred to WP3; WP2 done (`4c76e89`, CI run 39688587818 green; Methods export). WP1b and WP3 done (`97777ca`, CI run 39692016834 green; `docs/TASKS.md`; schema 9.0.4). Review remediation of WP3 (spend persistence, model-aware reuse, ECA checkpoint completeness, long-step lease) in `a5bd15a`, CI run 39703364453 green. Documents committed in `0bca07f`. Next: WP4 (needs D16) and WP5, opened by the owner; WP14 expert cases (owner).

Supporting documents (all proposals, same status):

| Document | Covers (spec section) |
|---|---|
| `M5_DESIGN_AMENDMENT_2.md` | **second review response**: claim-level precedence, credibility separate, inspection records for negatives, exploratory mode, delta screening without a threshold, I21, CAS/SQLite contract with I22, provider capability profile, controlled baselines, WP14 gate, approval scope |
| `M5_WP14_TRUTH_TABLE_DRAFT.md` | **for direct audit**: 22-row precedence table by claim kind, counterexamples, expert-case procedure, invariants I-1…I-9, two-layer Gate M measurement, revised fixtures |
| `M5_DESIGN_AMENDMENT_1.md` | **first review response** (partly superseded by Amendment 2): pre-emption truth table, decision granularity and workload, cold start, corpus snapshots (I20), evidence dimensions, Gate M components, transactions, hard budget, export fixes, fixtures F1–F16 |
| `M5_AUDIT.md` | baseline, CI status and the `_json_arg` failure, full capability audit table (1, 2) |
| `M5_TASKS_AND_DATA_MODEL.md` | ResearchTask, ResearchProblem (D16), state machine, concurrency, budget, schemas, migrations, API/CLI (3, 4) |
| `M5_CONTRIBUTION_DISCOVERY_PROTOCOL.md` | framing, landscape, progressive retrieval, candidates, adversarial verification, feasibility, decisions (5) |
| `M5_KNOWLEDGE_AND_INTEGRATION.md` | knowledge accumulation, domain workflows, integration with Methods/Results (6, 7, 9) |
| `M5_EXPORT_CONTRACT.md` | readable outputs for all five workflows (8) |
| `M5_GATE_M_AND_BENCHMARKS.md` | Gate M A–J, metrics, test matrix, workload cost benchmark (10, 11) |
| `M5_SECURITY_COPYRIGHT_HISTORY.md` | verified exposure, D20, key rotation, PDF rights, security tests (12) |
| `M5_WORK_PACKAGES.md` | WP0–WP13 with the 15 fields each, effort (14) |

---

## 1. Executive decision

1. **Fix CI first.** `main` has been red since the M3 push (one test, Linux only, `_json_arg` → `OSError: File name too long`; verified in the CI log of
   run 39576120896). The M3/M4 "all tests pass" statements were based on local Windows runs. WP0 (half a day) comes before anything else, and from
   now on a milestone is reported only with a green CI run id.
2. **Build M5 on the existing core, with no parallel store.** Tasks, problems, candidates and decisions are StateStore objects; task step state is
   derived from `workflow_runs`; invalidation reuses `graph/dependency.py`. The only foundation change is an `expected_head` check in `Tx.add_revision`,
   because engine writes have no optimistic concurrency today (audit B).
3. **Contribution Discovery is a corpus-bounded learning aid, not a novelty oracle.** Its statuses are `CHALLENGED`, `PARTIALLY_ADDRESSED`,
   `NOT_CHALLENGED_IN_SEARCHED_CORPUS`, `INSUFFICIENT_COVERAGE` and `NOT_ASSESSED`, derived deterministically from researcher-confirmed adjudications plus
   a coverage block. Discovery (generation) is separate from confirmation (adversarial verification + ECA).
4. **First deliverable after CI: the readable exporter** (starting with Methods/Gate K files). It needs no D-decision, every later package depends on
   it, and it lets the owner read the `GATEL_REAL` results for the Gate K continuation without opening SQLite.
5. **Release recommendation (D18):** keep 9.2.0's declared gates A–L unchanged and ship M5 with Gate M as **9.3.0**. 9.2.0 is not released (no tags),
   so either is possible, but adding a gate after M1–M4 were planned against A–L moves the target, and Gate K/L real evidence is still outstanding.
6. Expected effort (after Amendment 2): **~70 developer-days** (≈50–98) and **~28–40 researcher-hours**; critical path ~44 days with parallel streams.
7. **Precedence is decided per contribution claim** (mechanism, finding, boundary, method, evidence, correction) against each competitor by a
   versioned table that the owner audits (WP14); credibility of the earlier work never decides precedence, it only yields residual opportunities;
   the candidate is summarised from its claims, never rejected as a whole (Amendment 2, WP14 draft).
8. New invariants: **I20** absence is relative to a corpus snapshot; **I21** reproduction and correct reading do not validate an inference;
   **I22** no SUCCEEDED run without its outputs and no reference to a missing blob.

## 2. Audit (summary; full table in `M5_AUDIT.md`)

* Reusable as is: StateStore and CAS, the run registry and fingerprint reuse, proposal gateway, quote matcher, reading protocols with caps,
  literature engine, introduction argument, ECA with D15 sampling and batch tools, lifecycle and library with pins and closure, playbook selection and
  condition evaluation, the dependency-invalidation engine, Gate K/L blindness and export patterns, adapters.
* To extend: `Tx.add_revision` (expected head), `workflow_runs` (task linkage), schema/vocabularies/relations, `knowledge_kind` + `_KIND_RULES`,
  `eca._counter_evidence` (fed by retrieval), `check_result_change_impact` (shallow today), domain specs (discovery step family), usage reporting (O13).
* To build: content retrieval (none exists: `grep -rniE "bm25|tfidf|embedding|openalex|crossref" research_harness` is empty), `ResearchProblem`,
  `ResearchTask` with planner/runner, candidates, adversarial verification, feasibility, decisions, idea history, readable exporter, Gate M.

## 3. Gaps the plan closes

| Gap | Evidence | Closed by |
|---|---|---|
| No goal entry point; workflows assume the researcher holds upstream outputs | `compare_contribution` needs a `ContributionArgument`; `run_literature` needs an explicit source list | WP3 (tasks), I19 |
| No problem object | `protocols/schema.py::_TYPE_PROPS` has `ResearchQuestion{text}` only | WP4 |
| No retrieval | grep above; `context.py` selects by graph scope | WP5 |
| No candidate generation or pre-emption check | — | WP7, WP8 |
| Outputs not readable | JSON/CAS only | WP2 |
| Ideas, disagreements, unexplained results are forgotten | `taxonomy/knowledge.json` kinds | WP10 |
| CI red; no concurrency check on engine writes | `M5_AUDIT.md` A, B | WP0, WP1 |
| Rights to send texts to a provider not recorded | `Source.license_note` only | WP13 |

**New invariant I19 — goal entry points.** A researcher states a goal; the suite plans steps from the capabilities it has, using accumulated knowledge,
and produces outputs that can be read, challenged and continued. No workflow requires that the researcher already holds another workflow's output;
when something is missing, a step either builds a minimal version (recorded as such) or stops and says what it needs.

## 4. Workflow specification

Five goals, each a planner over existing capabilities (goal specs are data; `M5_TASKS_AND_DATA_MODEL.md` §1, §5):

| Goal | Entry statement | Steps (capabilities) | Mandatory checkpoints |
|---|---|---|---|
| `discover_contribution` | ResearchProblem | frame → retrieve → landscape → generate → adversarial retrieve → verify (+ECA) → feasibility → decide | framing choice; candidate decisions |
| `review_literature` | Problem or RQ | retrieve → `run_literature` (theory/method/contribution) → export | reading-list approval |
| `evaluate_methods` | candidate or RQ | `playbook draft` → `adjudicate run-pending` → sheet/confirm → validity → `plan draft` → fidelity | adjudication decisions; validity confirmation |
| `analyze_results` | AnalysisPlan | `plan run` → diagnostics → claim change → promise check | claim-change confirmation |
| `assess_claims` | Claims / manuscript | ECA coverage → run-pending → sheet → confirm | adjudication decisions |

Discovery in detail: `M5_CONTRIBUTION_DISCOVERY_PROTOCOL.md`.

## 5. Data model and migrations

`M5_TASKS_AND_DATA_MODEL.md`: new types `ResearchTask`, `ResearchProblem`, `ContributionCandidate`, `ResearchDecision`, `RetrievalRun`; nullable
`workflow_runs.task_id`/`task_step_id`; vocabularies in a new `taxonomy/discovery.json`. **No new graph relations**: `edges.relation` has a SQL CHECK
(`state/schema.py:156`) that SQLite cannot alter without rebuilding the table, so links use the existing `depends_on` / `derived_from` with an
`edge_kind` in the edge provenance, and `supports` / `challenges` once `ContributionCandidate` is a hypothesis type. All migrations additive; existing
objects never rewritten.

Change from the earlier draft: candidates are **project hypotheses** (`ContributionCandidate`), not library `KnowledgeItem`s; the library receives
idea-history items *about* them only through retrospective and explicit promotion.

## 6. Retrieval

Progressive and recorded: R1 BM25 (stdlib, deterministic) → R2 model query expansion (stored, then deterministic search) → R3 in-corpus citations and,
opt-in (D17), OpenAlex/Crossref metadata → R4 candidate-specific adversarial retrieval → R5 researcher review. Metadata-only records are never evidence.
Every run stores a coverage block (corpus digest, sizes, queries, exclusions, API calls, known gaps). Alternatives and the benchmark design are in the
discovery protocol §5.3.

## 7. Discovery protocol

See `M5_CONTRIBUTION_DISCOVERY_PROTOCOL.md` §5.1–5.7. Key rules: several framings kept; landscape separates author claim / evidence permits / suite
inference; candidates need existing gap entries and are generated after prior attempts are listed; verification over ten dimensions, every "already
does" statement is a HIGH ECA statement; status table deterministic; `INSUFFICIENT_COVERAGE` is a separate status, `MISSING_FULL_TEXT` is not (it is one
reason inside it); "novel" never appears.

## 8. Human review

| Checkpoint | What the researcher sees | What they do | Typical volume (pilot) |
|---|---|---|---|
| Plan approval | steps, inputs, checkpoints, cost estimate with basis | approve / revise / set budget | 1 per task |
| Framing | interpretations (own + proposed) with differences | choose ≥1, add own | 1 |
| Reading list (R5) | hits with stage and reason, acquisition list | exclude with reason, add papers, import full texts they may use | 1–2 |
| Competitor assessments (Amendment A2) | one row per (candidate, competitor): core-claim relation, sensitive facets with passages or, for negative relations, the closest passages of the competitor; the derived verdict | a verdict per sensitive facet + one rationale (no default); rows of an already CHALLENGED candidate optional | 20 rows, ~28 facet verdicts for 4 candidates × 5 competitors in the typical scenario (A2.3) |
| Candidate decisions | status, what is left, feasibility, prior attempts | keep / drop / merge / split / narrow / change mechanism / read more / go to methods / revisit when results change, with rationale | 1 per candidate |

Batch tools follow `eca.review_sheet` / `confirm_batch`: items needing attention first, decisions empty, one rationale per item.

## 9. Knowledge accumulation · 10. Integration · 11. Export · 12. Security · 13. API/CLI

* Knowledge: `M5_KNOWLEDGE_AND_INTEGRATION.md` §6 (five epistemic categories kept apart; new kinds; structured revisit conditions; mis-transfer tests).
* Domain workflows: same document §7 (generic engine; domains add data, cannot remove mandatory checkpoints).
* Integration: same document §9 (dependency graph, selective invalidation table, back-propagation).
* Export: `M5_EXPORT_CONTRACT.md` (provenance class per row, `NOT_AVAILABLE`, no secrets, no full text, reproducible from state digest; manual default
  with an automatic decision-report preview at checkpoints).
* Security: `M5_SECURITY_COPYRIGHT_HISTORY.md`.
* API/CLI: `M5_TASKS_AND_DATA_MODEL.md` §10.

## 14. Work packages (detail in `M5_WORK_PACKAGES.md`)

| WP | Title | Expected days | Depends on | Needs decision |
|---|---|---|---|---|
Efforts after Amendment 1 (A11):

| WP | Title | Expected days | Depends on | Needs decision |
|---|---|---|---|---|
| WP0 | CI repair, CI-status rule | 0.5 | — | — |
| WP1 | Store foundations (expected head, schema, vocab, run linkage, **stage-then-commit, orphans, fix `run_protocol` / `eca.adjudicate`**, lease field) | 3.5 | WP0 | — |
| WP2 | Readable exporter core (+ Methods/Gate K files; **path rule, two manifests, closure digest**) | 4 | WP0 | — |
| WP3 | ResearchTask core (+ **hard budget, lease**) | 6 | WP1 | — |
| WP4 | Problem representation and framing | 2 | WP1 | D16 |
| WP5 | Retrieval and coverage (+ **floor, corpus snapshots, delta screening**; R3b external: +2) | 7 (+2) | WP1 | D17 for R3b only |
| WP6 | Landscape (+ **evidence dimensions, method assessment record**) | 5.5 | WP2, WP3, WP5 | — |
| **WP14** | **Claim-level precedence table as versioned data, counterexamples, ≥20 expert cases, invariants I-1…I-9 (owner audit = approval gate for WP7–WP13)** — Amendment 2: 3 dev-days + 6–10 researcher hours | 3 | WP1 | D24 (thresholds) |
| WP7 | Candidate generation (+ core claim / locus fields) | 3.5 | WP6, WP14 | — |
| WP8 | Adversarial verification: `CompetitorAssessment`, truth table, sensitivity loop, judgement sheet | 8 | WP5, WP7, WP14 | — |
| WP9 | Feasibility and decisions (+ status lattice) | 4.5 | WP8 | — |
| WP10 | Idea history and revisit | 3.5 | WP1 | — |
| WP11 | Gate M synthetic (fixtures F1–F16, tests K, L, N) | 5 | WP2–WP10, WP14 | — |
| WP12 | Gate M real (five components, baseline, blind rating, held-out) | 3 + researcher | WP11 | D18, D19, D23, D24, D25 |
| WP13 | Integration with K/L, rights, security tests | 4 | WP1, WP2 | — |

## 15. Test matrix · 16. Gate M protocol · 17. Cost benchmark

`M5_GATE_M_AND_BENCHMARKS.md`: §5 test matrix (per area, named tests), §3 Gate M A–J each with setup / inputs / outputs / correct-if / fails-if /
artefact, pass rules for synthetic and real Gate M, §4 real-pilot scope (Zavitsanos 2025, Bao 2020 + erratum; pre-emption detection on those papers only,
not retrieval recall at scale), §6 workload cost formulas, estimate vs actual, ECA tiering by impact that does not lower any method-decision tier.

## 18. Migration and compatibility

* Additive migrations M5-1..M5-4; M4 stores open and migrate; M4 code refuses M5 stores (documented as forward-only in `docs/MIGRATION_9_1_TO_9_2.md`).
* Existing CLI commands and their outputs unchanged; Gate K/L JSON contracts unchanged (readable files are added alongside).
* Existing evaluation state (`sample/gate L/_eval_home/`, Gate K files) is never migrated by tests; the owner migrates a copy when ready.
* Reading-protocol data gains `RP_LANDSCAPE`; RP_METHOD v4 untouched, so existing extraction caches stay valid.

## 19. Risk register

| # | Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|---|
| R1 | CI stays red, hiding new regressions | was certain | high | WP0 first; milestone reports need a green run id | dev |
| R2 | Retrieval misses the pre-empting paper (synonyms, different field) | medium | high (false NOT_CHALLENGED) | R2 expansion, R4 per candidate, R5 researcher additions, `INSUFFICIENT_COVERAGE`, statuses always corpus-bounded | dev + researcher |
| R3 | Researcher over-reads NOT_CHALLENGED as "novel" | medium | high | status wording, coverage block next to every status, test bans "novel" | dev |
| R4 | Model self-confirmation (generator and verifier agree) | medium | medium | separate prompts and inputs; verifier sees candidate fields only; ECA confirmation by researcher | dev |
| R5 | Confirmation workload too high | medium | medium | tiering (status-deciding = HIGH, others sampled), batch sheets, caps on competitors | dev |
| R6 | Small real corpus makes Gate M real weak evidence | high | medium | report scope honestly; Gate M real tests pre-emption on held papers only | researcher |
| R7 | Sending full texts to a provider without the right | low–medium | high (legal) | `rights.send_to_provider`; refusal for NOT_ALLOWED; UNKNOWN needs explicit flag | researcher |
| R8 | Prompt injection through paper text | low | medium | gateway static check, quote verification, deterministic status derivation; test | dev |
| R9 | Scope creep delays Gate K/L completion (M4) | high | high | M5 WP0–WP2 help M4; WP3+ start only after owner approval; M4 researcher steps are independent | owner |
| R10 | External API changes / rate limits | medium | low | opt-in, recorded responses, never required | dev |
| R11 | Concurrency change breaks existing writers | low | medium | `expected_head` optional, default off; full suite | dev |
| R12 | Exposed key misused before rotation | unknown | medium (cost) | owner rotates now; adapter reads env only | owner |
| R13 | The pre-emption truth table itself encodes a wrong scientific judgement (e.g. row 7: same claim in another market without a reason = pre-empted) | medium | high (wrong statuses stored and reused) | table held as data, reviewed by the owner (WP14) before any use; every row has a fixture; pair verdicts keep the facets so a later table revision re-derives statuses | owner + dev |
| R14 | Researcher load for 100% pair confirmation (20 rows per typical task) leads to rubber-stamping | medium | high | no default, per-facet verdicts, closest passages shown, optional rows after CHALLENGED, time recorded (MU) | owner |

## 20. Decisions D16–D22

| Decision | Options | Architectural impact | Scientific impact | Cost | Risks | Recommendation | Owner approval needed | Blocks M5? |
|---|---|---|---|---|---|---|---|---|
| **D16** problem representation | A `ResearchProblem` type; B `ResearchQuestion{status: exploratory}` | A: one new type, no change to RQ consumers; B: every RQ consumer must filter | A keeps "not yet a question" explicit, multiple framings, history via `derived_from` | A +0.5 day | B: stages open on a non-question (`facts_from_store`) | **A** | yes | WP4 only (WP3 can start with RQ goals) |
| **D17** external metadata | none; OpenAlex/Crossref opt-in metadata; full-text APIs | opt-in module, recorded responses | better coverage reporting; still no evidence from metadata | +2 days | API drift, rate limits | **opt-in metadata only**, never full text | yes | WP5 R3b only |
| **D18** release placement of Gate M | Gate M in 9.2.0 (gates A–M); 9.2.0 on A–L and M5 as 9.3.0 | none on code | avoids moving the target after M1–M4 | — | delaying 9.2.0 by ~10 weeks if in 9.2.0 | **9.3.0** (9.2.0 not released, but A–L were declared) | yes | no (release planning) |
| **D19** author of the blind pre-emption reference | researcher; second reader; assistant draft adopted | none | an assistant-drafted reference is not a blind human reference (lesson of O2) | researcher hours | adoption of an AI draft weakens Gate M as it did K | **researcher writes it**, optional second reader; assistant prepares the blank form only | yes | WP12 only |
| **D20** history of `ae18cbb` | keep; rewrite + force-push; make private | rewrite invalidates clones (0 forks today) | none on blindness (system never read it) | rewrite ~0.5 day + coordination | rewritten commits can stay reachable by hash | **keep**, record in Gate K report; optionally make repo private | yes | no |
| **D21** Bao 2020 rerun route | Octave in WSL (sudo); MATLAB; Python RUSBoost port as declared *adaptation*; no rerun (data-only Study B) | none for M5 | a Python port is not a faithful MATLAB replication; a registered package is not a rerun | 1–4 days | port mistaken for replication | **Octave if sudo is available, else declared adaptation**, reported as such | yes | **no** (Gate L Study B only; Discovery unaffected) |
| **D22** default caps policy | per-paper (`reconsider --cap`); protocol defaults reviewed per domain; adaptive by paper type | data only | caps change what is extracted; decisive fields must not be truncated silently (`over_cap` listed) | 0.5 day | missing a decisive element | **protocol defaults + listed over-cap records + per-paper override** (status quo formalised) | yes | no |

| **D23** (Amendment 1) real Gate M corpus | pilot only (2–3 papers, `--accept-small-corpus`); adversarial corpus ≥15 usable full texts | none | pilot only cannot test `NOT_CHALLENGED`, retrieval recall or false pre-emption at a useful size | researcher time to select papers and record rights | pilot-only results over-read | **adversarial corpus** (terminology families, competing findings, the Bao erratum, one held-out competitor) | yes | WP12 only |
| **D24** (Amendment 1) thresholds | Gate M component thresholds and `min_fulltext_sources`, declared before the run | data only | thresholds fixed before results avoid tuning to the outcome | — | thresholds set too low | defaults in Amendment A6/A3 | yes | WP14 review, WP12 |
| **D25** (Amendment 1) candidate-quality baseline | B0 direct prompting; B0 + B1 researcher's own candidates | none | without a baseline, quality of generated candidates cannot be judged | +1–2 researcher hours for B1 | rating bias if not blinded | **B0 + B1**, blind rating | yes | WP12 only |

Amendment 2 §B9 refines D23 (≈15 papers is a starting point with required categories, never enough to support novelty beyond the corpus), D24
(separate locked dimensions: novelty relative to corpus, significance, feasibility, reasoning quality, retrieval recall, false/missed precedence,
evidence fidelity, coding accuracy, usefulness) and D25 (B0 gets the same corpus, budget and inputs; B1 can be rated blind only by a second rater).

Blocking M5 development: **D16** (WP4), **D24** (at the WP14 review) and, only for the external part of retrieval, **D17**. Blocking Gate M real:
**D18, D19, D23, D25**. Not blocking: D20, D21, D22.

## 21. Open items O1–O13 (+ O14, O15 found in this audit)

| # | Item | State | Evidence | Priority | Area | Depends on | Next step |
|---|---|---|---|---|---|---|---|
| O1 | Gate K continuation on `GATEL_REAL` | open | `PB_ZAV` rev 3: 30 elements, 28 decisive; no confirmed adjudications yet | P0 | Gate K | researcher; WP2 helps reading | `adjudicate run-pending`, your decisions via `confirm-batch`, validity assessment, plan, scores |
| O2 | Gate K reference annotation | open (limitation) | `REFANN_ZAVITSANOS2025` stored blind w.r.t. system output, **but adopted from an AI draft**: it is not an independent blind human annotation; 7 paper-code differences open (`gate_k_paper_code_differences_OPEN.json`) | P0 | Gate K | researcher | adjudicate D1–D7; report K2 with the adoption stated; optionally a second reader |
| O3 | Gate L | open | Study A reproduced; Study B data differ from Table 1 (146,045 vs 153,026 firm-years) | P0 | Gate L | researcher, D21 | blind Bao annotation, rubric, gold sets, Study C |
| O4 | Bao 2020 model rerun | open (limitation) | MATLAB code; `PKG_BAO_JAR2020` FAILED_TO_REPRODUCE against Table 1. **A registered package does not mean the model was rerun; a Python port is not a faithful MATLAB replication** | P1 | Gate L | D21 | choose route |
| O5 | `ae18cbb` exposure | open | 5 files, no secrets (security doc §1) | P1 | maintenance-security | D20 | owner decision |
| O6 | API key rotation | open | key appeared in the conversation | P0 | maintenance-security | owner | rotate in the provider console |
| O7 | Section-label inheritance pulls result statements into method extraction | open | e.g. element E26 "HRF remains the top-performing model" | P1 | Gate K / M5 (landscape uses sections too) | — | result-section exclusion for RP_METHOD or `is_method_choice` check; test on the pilot text |
| O8 | Element caps | partly resolved | `reconsider --cap` exists (`reading_protocols.reconsider`) | P2 | Gate K | D22 | formalise policy |
| O9 | Counter-evidence from `challenges` edges only | open | `eca._counter_evidence` | P1 | M5 | WP5, WP8 | retrieval proposes counter-evidence candidates |
| O10 | Claim effects entered by researcher only | open | `docs/ANALYSIS_PIPELINE.md` limits | P2 | M5 (analyze_results goal) | WP3 | optional drafting step, proposals only |
| O11 | PRISMA 27-item checklist | open | flow counts only (`literature.prisma_counts`) | P2 | M5 (review_literature export) | WP2 | only if systematic reviews are used |
| O12 | Anthropic live adapter | open | fake client tests only | P2 | maintenance | a key | live smoke test |
| O13 | Usage totals split | open | registry vs tracker | P1 | M5 | WP2 | `cost_report.md` merged view |
| O14 | CI red since M3 | open | run 39576120896 log | P0 | maintenance | — | WP0 |
| O15 | Engine writes lack optimistic concurrency | open | `Tx.add_revision` has no expected revision; `lifecycle._write_item` | P1 | M5 foundation | — | WP1 |
| O18 | Execution DAG registers the run before writing Result objects | open (found during WP1a) | `execution/dag.py` `register_run` then `_result_objects`; a crash leaves an execution run without Results (re-derivable from its artefact) | P2 | Results | — | group in the Results work |
| O17 | Orphan-blob reports scan only top-level content keys | open (found in second review response) | `StateStore.recover` and `validation/integrity.py` (rule 9, and the `REFERENCED_ARTIFACT_MISSING` check) look for `artifact_sha256`/`blob_sha256` only at the top level of object content; nested digests exist (`closure.artifact_digests_in`). Effects: referenced blobs may be reported as orphans (INFO), and a missing nested blob is not reported. No cleanup exists, so no data is at risk today | P1 (before any cleanup command) | maintenance | — | WP1 (Amendment 2 §B7) |
| O16 | Run registration and outputs written in separate transactions | open (found in review response) | `run_protocol`: passages then run; `eca.adjudicate`: SUCCEEDED run then record | P1 | M5 foundation / maintenance | — | WP1 (Amendment A7.2) |

## 22. Dependency graph, roadmap, critical path

```
WP0 ─► WP1 ─┬─► WP3 ─┐
            ├─► WP4 ─┤(D16)
            ├─► WP5 ─┼─► WP6 ─► WP7 ─► WP8 ─► WP9 ─┐
            ├─► WP10 ┘          ▲                   ├─► WP11 ─► WP12 (D18, D19, researcher)
            └─► WP13 ───────────┼───────────────────┘
WP0 ─► WP2 ─────────────────────┘
```

* **Parallel**: after WP1, streams {WP3}, {WP5}, {WP2→WP13}, {WP4, WP10} are independent (different files). Sequential spine: WP6 → WP7 → WP8 → WP9 → WP11 → WP12.
* **WP14** (truth table as data) runs alongside WP3/WP5 right after WP1 and must be reviewed by the owner before WP7 starts.
* **Critical path (expected, after Amendment 2)**: WP0 0.5 + WP1 4 + max(WP3 6, WP5 7.5, WP2 4) + WP6 5.5 + WP7 3.5 + WP8 9.5 + WP9 4.5 + WP11 5 + WP12 3.5 =
  **~44 dev-days** with parallel streams; **~70 dev-days** for one developer doing everything (≈50–98). (Amendment 1: 41 / 64; first draft: 29 / 49.)
  The owner's audit of WP14 (expert cases) is on the critical path before WP7.
* **Researcher time**: decisions D16–D22 (~1 h), framing and reading list for the synthetic demo review (~1 h), checkpoint reviews (~3 h), Gate M real
  statement + blind reference (~4 h), adjudications and decisions on the real run (~3–5 h), matching sheet (~1 h): ~14 h expected.

## 23. Milestone checklist

| Milestone | WPs | Checkpoint (owner) | Evidence required |
|---|---|---|---|
| **M5.0** CI green | WP0 | none | green CI run id on all four jobs |
| **M5.1** foundations + readable Methods export | WP1, WP2 | owner exports `GATEL_REAL` Methods files on their machine and reads them (optional, helps O1) | export of synthetic chain reproducible; migration test |
| **M5.2** tasks + problem | WP3, WP4 | D16 answered; owner runs a `review_literature` task on scripted or live adapter | task tests; crash/resume test |
| **M5.3** retrieval + coverage | WP5 (+R3b if D17) | owner reviews retrieval benchmark numbers | recall@10/precision@10/MRR baseline recorded |
| **M5.3b** truth table review (approval checkpoint for WP7–WP13) | WP14 | owner reviews the 25 rows, fixtures F1–F13, D24 thresholds | completeness test green; every fixture verdict as expected |
| **M5.4** landscape + candidates + idea history core | WP6, WP7, WP10 | owner reads a synthetic landscape and candidate file | landscape/candidate tests; F15; revisit/context-mismatch tests |
| **M5.5** verification, feasibility, decisions, integration | WP8, WP9, WP13 | owner tries the assessment sheet on synthetic data | F1–F14 at pair and status level; crash points C1–C6; security tests |
| **M5.6** Gate M synthetic | WP11 | — | all synthetic components and process tests green on CI |
| **M5.7** Gate M real | WP12 | D18, D19, D23, D25; problem statement; blind references (interpretations, competitors, pair verdicts, held-out choice); ratings; decisions | `gate-validate M` passes; five components reported separately |

Each milestone ends with a report quoting the CI run id; nothing proceeds past a checkpoint the owner has not cleared.

## 24. Open questions for the owner

1. D16–D22 as in §20 (ordered by impact in the reply).
2. Which problem statement should the real Gate M use, and does the owner want a second reader for the reference?
3. Is a price table available (per provider) so cost reports show money, or tokens only?
4. Should the Gate K working folder be added to `.gitignore` (changes a tracked file)?
5. May full texts of the pilot papers be sent to DeepSeek (rights `send_to_provider`)? CC BY says yes for Zavitsanos 2025; Bao 2020 depends on the
   owner's licence.
6. Should M5 wait until Gate K continuation (O1) is done, or run in parallel (M5.0–M5.1 help O1; recommended in parallel)?

## 25b. What Amendment 2 changed (response to the second design review)

* Precedence per contribution claim (six kinds) instead of per candidate; the row-7 case now gives "mechanism preceded, finding builds on, boundary
  assessed separately", summary `MIXED`.
* Credibility (`p_quality`) removed from the precedence table; it produces residual opportunities only (invariant I-1).
* Negative facets need an inspection record; material known to be missing forces `U`.
* Delta screening covers every new source; 30 is a batch size; statuses stay `STALE_FOR_CORPUS` with progress until done.
* Exploratory ideas allowed without a corpus, labelled ungrounded, never with an assessed status.
* I21 and I22 added; CAS/SQLite contract reuses the existing staging protocol; cleanup only with a recursive reference scan, dry-run, grace period;
  retries after a crash are not claimed to be free; acceptance by kill-9 crash injection on Linux.
* Provider capability profile with sourced, probe-confirmed limits (none verified yet).
* D23–D25 as revised: adversarial corpus composition, locked multi-dimension rubric, B0 with the same corpus and budget, blind rating (B1 blind only
  with a second rater).
* WP14 is an approval gate with three products; effort ≈70 days, researcher 28–40 h.

## 25a. What Amendment 1 changed (response to the design review)

* Pre-emption decided by a truth table over the candidate's core claim and locus; false pre-emption and missed pre-emption both have fixtures.
* Decision unit is the (candidate, competitor) assessment; HIGH = `claim_rel` + facets sensitive under the truth table; negative relations judged by
  the researcher with the closest passages; workload table for four scenarios.
* Cold start split into four cases; no candidates below a corpus floor.
* Invariant I20: absence-based conclusions bound to a corpus snapshot; delta screening independent of retrieval rank.
* `evidence_permits` replaced by reading / inference / reproduction dimensions.
* Gate M split into five components with a baseline, blind rating and a held-out competitor; false pre-emption measured.
* Stage-then-commit transactions; two existing non-atomic sites found and assigned to WP1.
* Hard per-call budget; task lease.
* Export path rule, deterministic manifest, task closure digest.
* New WP14, decisions D23–D25, item O16; effort 64 expected.

## 25. What changed against the first M5 draft (same file, earlier today)

* CI found red and diagnosed; WP0 added as the first step; milestone reports must cite a CI run.
* Concurrency gap found (`Tx.add_revision`); WP1 adds `expected_head`.
* Candidates moved from library `KnowledgeItem`s to project `ContributionCandidate`s; library gets idea-history items only via retrospective.
* Retrieval made progressive (R1–R5) with a benchmark, external metadata boundaries and a coverage block; new status `INSUFFICIENT_COVERAGE`;
  ten verification dimensions; deterministic status table.
* Landscape separates author claim / evidence permits / suite inference.
* Decisions extended to nine actions bound to object revisions, with revisit conditions; structured revisit evaluation.
* Export contract covers all five workflows with provenance classes, `NOT_AVAILABLE`, secret and path rules, reproducibility check.
* Gate M expanded from CD1–CD4 to A–J with metrics and explicit pass rules; synthetic and real separated; D18 recommendation changed from "Gate M blocks
  9.2.0" to "M5 + Gate M as 9.3.0".
* PDF rights split into download / use / store / send-to-provider / export.
* Selective invalidation reuses `graph/dependency.py` instead of new machinery; no new graph relations (SQL CHECK on `edges.relation`).
* Effort re-estimated from 21–27 to 36–70 days (expected 49).
