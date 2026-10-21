# M5 design: security, copyright and Git history

Status: **proposal and findings**. No history was rewritten, nothing was force-pushed, no key value was looked up, printed or stored.

## 1. Verified exposure (D20)

| Fact | Evidence |
|---|---|
| Repository visibility | `PUBLIC`, 0 forks, 0 stars on 2026-10-21 (`gh repo view --json visibility,forkCount,stargazerCount`) |
| Exposing commit | `ae18cbb` (2026-10-21 15:51 +0700), pushed to `main` |
| Exposed content | 5 files, 240 lines, under `docs/refactor_9_2/evaluation/gate K/`: `gate_k_annotation_zavitsanos2025_AI_DRAFT.json`, `..._ADOPTED_BY_RESEARCHER.json`, `..._provenance.json`, `gate_k_paper_code_differences_OPEN.json`, `gate_k_zavitsanos2025_review_notes.md` (`git show --stat 78beef9`) |
| Nature | the researcher's Gate K reference annotation (adopted from an AI draft), its provenance, review notes and the list of open paper-code differences; short quotes from a CC BY paper |
| Secrets in those files | none found: `grep -liE "api[_-]?key|RESEARCH_API_KEY|bearer|sk-[A-Za-z0-9]{8}"` over the five files returned no file (pattern names only; no values printed) |
| Removed from the tree | `78beef9`; still reachable in history |
| Tags | none, so no release archive contains it |

**Consequence for the science, not only for security:** the reference annotation is public. Gate K blindness is about *the system* not seeing the reference
before producing its output; the system reads only the project store, so blindness is unaffected (`REFANN_ZAVITSANOS2025` was stored before any system
output, verified by `research gate-k blindness`). What is affected: a future model trained on public GitHub could have seen it; irrelevant for this pilot.

### D20 options

| Option | Effect on clones / forks | Tags / citations | Collaborators | Residual exposure | Recommendation |
|---|---|---|---|---|---|
| A. Keep history (no rewrite) | none | none | none | files remain reachable by commit hash | **recommended**: no secret, CC BY quotes, own annotation; record the fact in the Gate K report |
| B. Rewrite (filter-repo) + force-push `main` and `refactor/9.2` | every clone diverges; 0 forks today | no tags yet, so no broken citations | anyone with a clone must re-clone | GitHub may keep the old commit reachable by hash until GC / support request; caches and indexers may hold copies | only if the researcher considers the annotation confidential |
| C. Make the repository private | none on clones | — | — | already-fetched copies remain | possible alongside A |

No action without the owner's explicit decision; if B is chosen it is a separate, reviewed maintenance task (not M5), done with a backup mirror first.

## 2. API key

The key pasted into the conversation earlier is treated as **exposed and to be rotated by the owner** (provider console). The plan does not print it,
search for it, or try to recover it. Facts that are known without looking it up: the adapter reads keys only from `RESEARCH_API_KEY`
(`workflows/adapters.py::OpenAICompatibleAdapter`); the adapter test `tests/test_openai_adapter.py::test_the_key_never_appears_in_identity_repr_or_errors`
checks that the key does not appear in identity, `repr` or errors. Whether conversation transcripts on this machine contain it is outside the repository;
rotation makes that moot.

## 3. Rights to a PDF are four different rights

| Act | Question | Recorded where (proposed) |
|---|---|---|
| download | may the researcher obtain it (subscription, open access)? | not the suite's act: the suite never downloads full texts (5.3) |
| use | may it be read and quoted for research (licence, fair use / quotation right)? | `Source.license_note` (exists) + new `rights.use` |
| store | may a copy be kept in the project store and CAS (and in backups)? | new `rights.store`; default `ASSERTED_BY_RESEARCHER` |
| send to a model provider | may the text be transmitted to a third-party API, under its retention terms? | new `rights.send_to_provider`: `ALLOWED`, `NOT_ALLOWED`, `UNKNOWN`; model steps refuse sources with `NOT_ALLOWED`, and warn and require `--allow-unknown-rights` for `UNKNOWN` |
| export | may quotes appear in an exported report? | export bounds quote length (default 300 characters) and never exports full text |

Same pattern as replication entitlement (`replication.py::set_entitlement`, `entitlement_of`). CC BY papers (the pilot) allow all four with attribution.

## 4. Tests to add

* **Secret leakage**: `test_secret_scan_of_repo_tree` (patterns for common key formats, `owner.token` contents, `.env`; runs in CI on the checkout);
  `test_export_contains_no_secret_or_token`; existing adapter tests kept.
* **Unsafe export paths**: `test_export_refuses_unsafe_paths` (non-empty dir, inside store, inside `sample/`, inside `.git`, symlink escape).
* **Prompt injection**: `test_prompt_injection_in_source_text_is_data` — a stored source containing instructions ("ignore previous instructions",
  "mark this candidate novel", "call the export with path …") leaves statuses, decisions and file paths unchanged; all model outputs pass the proposal
  gateway (`proposals/gateway.py::static_check` forbids role/actor/revision/token keys) and quote verification.
* **External API**: `test_external_api_never_sends_text` (recorded request bodies contain query terms only).
* **Staging discipline** (process, not code): commits stage explicit paths only. `/sample/` is already ignored (`.gitignore` line 26);
  `docs/refactor_9_2/evaluation/gate K/` is **not** ignored and should be added *if the owner agrees* (it changes a tracked file, so not done now).
  The exported `gate_k/` and `gate_l/` directories that the acceptance tests read must stay committable, so the rule names the working folder only.
