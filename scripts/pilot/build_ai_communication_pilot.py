"""Build the isolated pilot setup for the critical literature review on AI-assisted communication (WP4-WP6.1 on real bibliographic metadata).

What it does, offline, from `seeds.json` and `seeds_verified.json` (the latter produced by `verify_seeds.py` against Crossref):

* creates an isolated project store under `pilots/ai_communication_critical_review/_store/` (git-ignored; deleted and rebuilt each time; never `sample/`, never `GATEL_REAL`);
* records the ResearchProblem with its inquiry (critical_synthesis + theory_development, systematic_review design, textual / conceptual / empirical evidence, management /
  organizational communication) and the non-goals the owner set;
* records the review protocol as a **DRAFT drafted by the assistant**: nothing is adopted, nothing is screened, no database search is claimed to have been run;
* imports each Crossref-verified seed as a **metadata-only** Source (no abstract, no full text), and does not import a seed that could not be verified;
* runs the internal retrieval over that corpus (it finds no full text, and says so);
* writes the deliverables, each labelled by how it was produced.

It does not call a model, download a full text, or screen a record. A landscape, assumptions, tensions and their inspection need lawfully held full texts and an approved model run;
those deliverables are written as NOT_AVAILABLE with the exact blockers. Status: REAL_CORPUS_VALIDATION_PENDING.

    python scripts/pilot/build_ai_communication_pilot.py
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research_harness.discovery import problem as P  # noqa: E402
from research_harness.discovery import retrieval as RT  # noqa: E402
from research_harness.export import render as R  # noqa: E402
from research_harness.export.manifest import write_export  # noqa: E402
from research_harness.export.retrieval import build_retrieval_export  # noqa: E402
from research_harness.export.review import build_review_export  # noqa: E402
from research_harness.knowledge import corpus  # noqa: E402
from research_harness.knowledge import review_protocol as RP  # noqa: E402
from research_harness.state.store import StateStore  # noqa: E402

PILOT = ROOT / "pilots" / "ai_communication_critical_review"
STORE_HOME = PILOT / "_store" / "home"
PROJECT = "PRJ_AICOMM_PILOT"
DOC_TYPE = {"journal-article": "journal_article", "proceedings-article": "conference_paper", "posted-content": "preprint", "book-chapter": "book_chapter"}
REVIEW_STATUS = {"journal-article": "PUBLISHED", "proceedings-article": "PUBLISHED", "posted-content": "NOT_REVIEWED", "book-chapter": "PUBLISHED"}
STATEMENT = ("Does the increasing capacity to produce AI-assisted communication challenge how existing scholarship understands the relationship between communicative participation, "
             "collective attention, and relational engagement at work?")
NON_GOALS = ["Do not assume that the literature conflates communicative participation with relational engagement: whether it does is to be checked in full texts.",
             "Do not assume that AI-assisted communication necessarily increases overload or reduces engagement.",
             "Do not turn the project into an empirical hypothesis-testing study.",
             "No novelty verdict: a tension is never declared unrecognised; recognition is stated only from an inspection of a stated set of sources."]
INQUIRY = {"research_purposes": ["critical_synthesis", "theory_development"], "research_design": "systematic_review",
           "evidence_forms": ["textual_evidence", "conceptual_argument", "empirical_results"], "discipline": {"domain": "management", "subfield": "organizational_communication"}}
DRAFTED_BY = "assistant:claude-sonnet-5-5 (pilot preparation, 2026-10-22)"


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def protocol(seeds: dict, verified_at: str, n_seeds: int) -> dict:
    streams = {
        "S1": ("How is collective attention conceptualised in organisations, and what is assumed to draw on it?", ["collective attention", "attention allocation", "attention-based view"]),
        "S2": ("How is communicative participation conceptualised (visibility, voice, silence), and what does a message count as?", ["communication visibility", "employee voice", "organizational silence"]),
        "S3": ("How are relational engagement and engagement at work conceptualised, and what is assumed to express them?", ["employee engagement", "social exchange", "high-quality relationships"]),
        "S4": ("What is known about messages produced with machine assistance, and about their relational effects?", ["AI-mediated communication", "machine-assisted messages"]),
        "S5": ("How are information and communication overload and the richness of media conceptualised?", ["information overload", "media richness", "hyperpersonal"])}
    return {
        "review_type": "critical_systematic",
        "streams": [{"id": k, "name": seeds["streams"][k], "question": q, "terms": t} for k, (q, t) in streams.items()],
        "search": {"sources": [
            {"name": "Seed list proposed by the assistant from its own knowledge; metadata verified against Crossref", "kind": "expert", "status": "executed", "records_identified": n_seeds,
             "queries": [{"stream": "ALL", "string": "no query: a list of candidate seed studies, not a search result", "date_run": verified_at[:10]}]},
            {"name": "Scopus, Web of Science, ABI/INFORM or equivalent (PLANNED: access and the run are the researcher's)", "kind": "database", "status": "planned",
             "queries": [{"stream": "S1", "string": "TITLE-ABS-KEY((\"collective attention\" OR \"attention allocation\") AND (organi*ation* OR team* OR \"shared channel*\"))"},
                         {"stream": "S2", "string": "TITLE-ABS-KEY((\"communication visibility\" OR \"employee voice\" OR \"organi*ational silence\") AND (\"social media\" OR \"enterprise social\" OR channel*))"},
                         {"stream": "S3", "string": "TITLE-ABS-KEY((\"relational engagement\" OR \"employee engagement\" OR \"high-quality relationship*\") AND (communication OR conversation))"},
                         {"stream": "S4", "string": "TITLE-ABS-KEY((\"AI-mediated communication\" OR \"machine-generated message*\" OR \"AI-assisted writing\") AND (work* OR organi*ation*))"},
                         {"stream": "S5", "string": "TITLE-ABS-KEY((\"information overload\" OR \"communication overload\") AND (organi*ation* OR work*))"}]},
            {"name": "Backward and forward citation chasing from the verified seeds (PLANNED)", "kind": "citation_chasing", "status": "planned", "queries": [{"stream": "ALL", "string": "references and citing works of each included seed"}]}],
            "date_from": "1970-01-01", "date_to": "2026-10-22", "languages": ["English"],
            "inclusion_scope": "peer-reviewed articles, proceedings papers, books and chapters, and working papers, in management, organizational communication and information systems; theoretical and empirical"},
        "screening": {"procedure": "TO BE DECIDED BY THE RESEARCHER: who screens, in which stages, how borderline records and disagreements are settled. Nothing has been screened.",
                      "stages": ["dedup", "screening", "eligibility"],
                      "criteria": {"include": ["conceptualises or tests a relation among communicative participation, collective attention or relational engagement at work (DRAFT: for the researcher to change)",
                                               "a full text can be lawfully held in the project"],
                                   "exclude": ["no theoretical or empirical treatment of the constructs (DRAFT)", "not about work or organisations, unless it is a foundational statement of a construct (DRAFT)"]}},
        "extraction": {"fields": ["theory invoked", "construct definitions", "stated assumptions", "mechanisms", "theoretical claims", "boundary conditions", "empirical findings (as supporting literature)"],
                       "protocols": ["RP_LANDSCAPE@2", "RP_THEORY@3"]},
        "synthesis": {"method": "reconstruct each theory, construct and stated assumption from full text at Source@revision; compare scopes, units and levels of analysis; propose possible tensions as unconfirmed proposals",
                      "how_tensions_are_judged": "a tension is proposed from quoted passages and stays unconfirmed until the researcher decides; whether the literature already addresses it is stated only from an inspection of a stated set of sources, never from a model's omission or a search that found nothing"},
        "limitations": ["appraisal is NOT declared: whether and how studies are appraised is the researcher's decision",
                        "the database search is planned, not run; the seed list is the assistant's, not a search result",
                        "no full text is held; nothing here has been read"]}


def main() -> int:
    seeds = json.loads((PILOT / "seeds.json").read_text(encoding="utf-8"))
    ver = json.loads((PILOT / "seeds_verified.json").read_text(encoding="utf-8"))
    by_key = {r["key"]: r for r in ver["results"]}
    if (PILOT / "_store").exists():
        shutil.rmtree(PILOT / "_store")                      # the pilot's own store, rebuilt each time
    store, token = StateStore.create(STORE_HOME, PROJECT, owner_id="researcher")
    (PILOT / "_store" / "token").write_text(token, encoding="utf-8")

    # ---- the problem
    pid = P.new_problem(store, token, statement=STATEMENT, kind="puzzle", scope={"domain": "management / organizational communication", "related_fields": ["information systems"],
                        "unit_of_analysis": "theories and their assumptions as stated in the literature"}, exclusions=NON_GOALS, inquiry=INQUIRY,
                        label="AI-assisted communication, participation, collective attention and relational engagement (critical review pilot)")["problem_id"]

    # ---- the verified seeds, metadata only
    imported, unverified = [], []
    for s in seeds["seeds"]:
        r = by_key[s["key"]]
        if r["status"] != "VERIFIED_CROSSREF":
            unverified.append(s["key"])
            continue
        typ = r["publication"]["crossref_type"]
        issued = r["issued"][0] if r.get("issued") else [r["year"]]
        date = "-".join(f"{int(x):02d}" if i else str(x) for i, x in enumerate(issued))
        corpus.import_metadata_only(store, token, citation_key=s["key"], title=r["title"], year=r["year"], authors=r["authors"][:12], venue=r["container"] or None, doi=r["doi"],
                                    document_type=DOC_TYPE.get(typ, "other"), review_status=REVIEW_STATUS.get(typ, "UNKNOWN"), first_public_date=date,
                                    date_evidence="Crossref record: issued date (date of first appearance is not established)",
                                    acquisition_note="full text NOT held; lawful access to be arranged by the researcher. Metadata verified against Crossref only.")
        imported.append(s["key"])

    # ---- the review protocol: a draft by the assistant, nothing adopted, nothing screened
    RP.set_review_protocol(store, token, pid, protocol(seeds, ver["verified_at"], len(seeds["seeds"])), "a first plan drafted by the assistant for the researcher to adopt or change; nothing screened, nothing searched beyond the seed list",
                           drafted_by=DRAFTED_BY)
    audit = RP.audit_review(store, pid)

    # ---- internal retrieval over the metadata
    terms = ["collective attention", "communicative participation", "relational engagement", "AI-mediated communication", "communication visibility", "employee engagement"]
    rid = RT.new_run(store, token, pid, terms=terms, label="Pilot reading list over verified metadata")["retrieval_id"]
    RT.search(store, token, rid)
    view = RT.run_view(store, rid)

    # ---- exporter outputs (production-generated by the suite exporters)
    rev_dir, ret_dir = PILOT / "review_export", PILOT / "retrieval_export"
    for d in (rev_dir, ret_dir):
        if d.exists():
            shutil.rmtree(d)
    write_export(store, build_review_export(store, pid), rev_dir, into_repo=True)
    write_export(store, build_retrieval_export(store, rid), ret_dir, into_repo=True)
    files: dict[str, tuple[bytes, str]] = {}
    for name in ("search_protocol.md", "corpus_inventory.csv"):
        files[name] = ((rev_dir / name).read_bytes(), "PRODUCTION_EXPORTER (research export review; copy of review_export/" + name + ")")

    # ---- script-generated from the store
    pv = P.problem_view(store, pid)
    inq = pv["inquiry"]
    files["problem_framing.md"] = (("\n".join([
        "<!-- origin: SCRIPT_FROM_STORE (scripts/pilot/build_ai_communication_pilot.py reads the ResearchProblem and writes this text); not a suite exporter -->",
        "# Problem framing", "", f"**{pid}** (revision {pv['revision']}, status {pv['status']}, kind {pv['kind']})", "", f"> {pv['statement']}", "",
        "## Inquiry (four separate facts, none inferred from another)", "",
        f"* purpose: {', '.join(inq['research_purposes'])}", f"* design: {inq['research_design']} (a critical literature review with systematic searching)",
        f"* evidence: {', '.join(inq['evidence_forms'])} (empirical findings enter as supporting literature when relevant)",
        f"* discipline: {inq['discipline']['domain']} / {inq['discipline']['subfield']}; related field: information systems (recorded in the problem's scope)", "",
        "## Scientific objective", "",
        "Reconstruct, compare and critically interrogate how the existing literature conceptualises the relations among communicative participation, collective attention and relational engagement at work.", "",
        "## What is not assumed", "", *[f"* {x}" for x in pv["exclusions"]], "",
        "## Interpretations", "", "None is recorded. The problem has not been framed into readings: no model was asked (that would be a call to a paid model), and the researcher has not written any.", "",
        "## What this pilot asks of the suite", "",
        "1. Can the review's method be recorded as a checkable protocol, with the researcher's adoption and decisions kept apart from an assistant's draft? (`docs/REVIEW_PROTOCOL.md`)",
        "2. Can constructs, theories, stated assumptions and mechanisms be reconstructed from lawfully held full texts at Source@revision, and compared, without the suite deciding who is right? (WP5, WP6, WP6.1)",
        "3. Can proposed theoretical tensions be inspected against a stated set of sources, with a recognition status that never claims absence from a model's silence? (inspection records)",
        "", "None of these is answered here: no full text is held and no model was run. See `limitations.md`.", ""])).encode("utf-8"), "SCRIPT_FROM_STORE")

    cols = ["key", "stream", "role (the assistant's reason for suggesting it)", "status", "DOI (as registered)", "registered title", "first author (as registered)", "year (as registered)", "container", "Crossref type",
            "publication label", "correction/erratum/retraction noticed", "notes from Crossref", "title similarity", "in project corpus", "full text held"]
    rows = []
    for s in seeds["seeds"]:
        r = by_key[s["key"]]
        ok = r["status"] == "VERIFIED_CROSSREF"
        near = (r.get("nearest") or [{}])[0]
        rows.append([s["key"], s["stream"], s["role"], r["status"], r.get("doi", ""), r.get("title", ""), (r.get("authors") or [""])[0], r.get("year", ""), r.get("container", ""),
                     r["publication"]["crossref_type"] if ok else "", r["publication"]["label"] if ok else "", r["publication"]["retraction_or_correction_noticed"] if ok else "",
                     "; ".join(r["publication"]["notes"]) if ok else (r.get("reason", "") + " Nearest candidates for a human to look at: " + "; ".join(f"{n['doi']} '{n['title'][:60]}' {n['year']} (similarity {n['similarity']})" for n in r.get("nearest", [])[:2])),
                     near.get("similarity", "") if ok or not r.get("nearest") else r["nearest"][0]["similarity"], s["key"] in imported, False])
    md = ["<!-- origin: SCRIPT_FROM_STORE and seeds_verified.json (Crossref lookups); the text of the notes is ASSISTANT_AUTHORED -->", "# Citation and evidence audit", "",
          f"Candidate seed studies: {len(seeds['seeds'])}. Verified against Crossref: {sum(1 for r in ver['results'] if r['status'] == 'VERIFIED_CROSSREF')}. Not verified: {len(unverified)} ({', '.join(unverified) or 'none'}). "
          f"Lookup time: {ver['verified_at']}. Rule: {ver['threshold']['rule']} (title similarity at least {ver['threshold']['title_similarity']}).", "",
          "## What this audit shows, and what it does not", "",
          "* It shows that a Crossref record exists whose registered title, first author and year match what the assistant believed. It does not show what the paper says, that it is relevant, or that it is the best source for a construct.",
          "* **No full text is held for any seed**, so no passage, quotation, theory, assumption or finding has been extracted from any of them. Nothing in this pilot quotes an author.",
          "* A Crossref `journal-article` type means the record is a journal article; it does not assert peer review. A `posted-content` record is a preprint or similar posting: for such a seed the published version is **not** verified here.",
          "* Registered years can differ by one from the print year (online-first dates). Corrections and errata that Crossref lists are flagged; a retraction check beyond Crossref was not made.",
          "* A seed that could not be verified was not imported into the corpus and must not be cited from this list until a human has matched it.", "", "## Seeds", ""]
    md += ["| key | stream | status | DOI | registered title | year | type | flags |", "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        md.append(f"| {row[0]} | {row[1]} | {row[3]} | {row[4] or '-'} | {R.cell(row[5] or '-', 90)} | {row[7] or '-'} | {row[9] or '-'} | "
                  f"{'CORRECTION/ERRATUM LISTED' if row[11] else ''}{' PREPRINT RECORD ONLY' if row[9] == 'posted-content' else ''}{' NOT VERIFIED' if row[3] != 'VERIFIED_CROSSREF' else ''} |")
    md += ["", "## Seeds with a note", ""]
    for row in rows:
        if row[12] or row[9] == "posted-content" or row[3] != "VERIFIED_CROSSREF":
            md.append(f"* **{row[0]}**: {row[12] or ''}" + (" The Crossref record is a posted preprint; the published version was not matched." if row[9] == "posted-content" else ""))
    files["citation_evidence_audit.md"] = (("\n".join(md) + "\n").encode("utf-8"), "SCRIPT_FROM_STORE")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    w.writerows(rows)
    seeds_csv = buf.getvalue().encode("utf-8")
    files["seed_verification.csv"] = (seeds_csv, "SCRIPT_FROM_STORE (seeds_verified.json)")

    # ---- not available: they need full texts and an approved model run
    blockers = ["No full text of any included study is held in the project (every Source is METADATA_ONLY). The researcher must supply lawfully accessible full texts.",
                "No approved model run: the landscape needs the reading protocols run by a model, which sends text to a provider; this needs the researcher's permission per source, a budget the suite can account for, and a rotated API key.",
                "Backlog B1 (partial provider usage) is still open: a live run could not be metered reliably enough for an uncontrolled batch.",
                "The researcher has not adopted the review protocol or screened any record."]
    for name, title, what in (("conceptual_map.md", "Conceptual map", "the constructs (communicative participation, collective attention, relational engagement) as each study defines them, with the theory each invokes and the level of analysis, reconstructed from full text at Source@revision"),
                              ("theoretical_assumptions.md", "Theoretical assumptions", "the assumptions each study states, those it says it tests, and any assumption a reader or the suite proposes as implied (kept apart from what authors wrote), each with its quote and place"),
                              ("tension_inspection_report.md", "Tension inspection report", "the proposed tensions among the reconstructed theories and, for each, the inspection of a stated set of sources: which address it (with verbatim evidence), which only mention the phenomenon, which could not be read")):
        doc = R.Doc(title, f"<!-- origin: SCRIPT_FROM_STORE using the suite's document renderer; this file reports that the content is not available, it contains no analysis -->")
        doc.unavailable("; ".join(blockers))
        doc.add(f"This file would contain {what}.", "", "## Why it is not available", "", *[f"* {b}" for b in blockers], "",
                "## What would produce it", "", "1. Full texts lawfully held and imported (`research corpus import`), screened and included by the researcher (`research review-protocol screen`, then `adopt`).",
                "2. `research landscape build RET_x` with an approved model and budget; then `landscape propose`, the researcher's review, `landscape candidates` and `landscape inspect`.",
                "3. `research export landscape LAND_x`, which writes the conceptual map, assumptions, relations and inspections from the store.", "",
                "Status: REAL_CORPUS_VALIDATION_PENDING. No synthetic result is substituted for it.")
        files[name] = (doc.text(), "SCRIPT_FROM_STORE (renderer NOT_AVAILABLE header)")

    fulltexts = sum(1 for g in view["reading_list"])
    lim = ["<!-- origin: ASSISTANT_AUTHORED text, written into the repository by the pilot script -->", "# Limitations and status", "",
           "**Status: REAL_CORPUS_VALIDATION_PENDING.** This pilot is a reproducible setup and a readiness report. It is not a validation of the suite on real literature.", "",
           "## What exists", "", f"* An isolated store (`{PROJECT}`) with the ResearchProblem and its inquiry, a DRAFT review protocol drafted by the assistant, and {len(imported)} metadata-only Sources whose metadata matched a Crossref record.",
           f"* An internal retrieval run over that metadata: {fulltexts} full texts on the reading list, {view['coverage']['known_gaps']['not_read_candidates']} possibly relevant papers listed as not read; below the floor of full texts, as it must be.",
           f"* A review audit: protocol {audit['protocol_status']}; ready to build a landscape: {audit['readiness']['can_build_landscape']}.", "",
           "## What does not exist, and why", "", *[f"* {b}" for b in blockers], "",
           "## What the assistant did and did not do", "",
           "* The seed list is the assistant's own selection from its knowledge, not a search result. Its titles, authors and years were checked against Crossref by `scripts/pilot/verify_seeds.py`; " + (f"{len(unverified)} seed(s) that did not match ({', '.join(unverified)}; see the audit) were left out of the corpus." if unverified else "every seed matched a record."),
           "* The review protocol, the draft inclusion and exclusion criteria, the planned database queries and the stream questions are the assistant's drafts. They count as the researcher's only after `review-protocol adopt`. The appraisal component is deliberately not declared.",
           "* No screening decision was made on anyone's behalf. No DOI was typed from memory: every DOI in this directory was returned by Crossref for a matching record. No quotation of an author appears anywhere.",
           "* No model was called. No full text was downloaded. Nothing was sent to a third party except bibliographic lookups to the public Crossref API (titles, first authors, years).", "",
           "## Methodological decisions that belong to the Research Owner", "",
           "1. Whether and how studies are appraised in this review (the appraisal component is undeclared).",
           "2. The inclusion and exclusion criteria, the screening procedure and who screens.",
           "3. Which databases are searched (the planned queries are drafts), and whether the seed list is a starting point or is discarded.",
           "4. Which seeds are screened in; in particular, for seeds whose Crossref record is a preprint, whether to find and cite the published version.",
           "5. Whether full texts may be sent to a model provider, for which sources, with which budget; and whether to rotate the key and close backlog B1 before a live run.",
           "6. Whether the three execution contracts describe how this kind of review is actually done (they are `EXTRACTED` skeletons).", "",
           "## How to reproduce", "", "```", "python scripts/pilot/verify_seeds.py --seeds pilots/ai_communication_critical_review/seeds.json --out pilots/ai_communication_critical_review/seeds_verified.json   # needs the network",
           "python scripts/pilot/build_ai_communication_pilot.py                                                                                         # offline, from seeds_verified.json", "```", ""]
    files["limitations.md"] = (("\n".join(lim)).encode("utf-8"), "ASSISTANT_AUTHORED")

    # ---- write the deliverables and the pilot manifest
    for name, (data, _origin) in files.items():
        (PILOT / name).write_bytes(data)
    exp_manifests = {d.name: sha256((d / "manifest.json").read_bytes()) for d in (rev_dir, ret_dir)}
    manifest = {"pilot": "ai_communication_critical_review", "status": "REAL_CORPUS_VALIDATION_PENDING", "labels": ["IMPLEMENTED", "TECHNICALLY_TESTED", "RESEARCHER_REVIEW_REQUIRED", "REAL_CORPUS_VALIDATION_PENDING"],
                "project_id": PROJECT, "problem_id": pid, "retrieval_id": rid, "seeds": {"candidates": len(seeds["seeds"]), "verified_against_crossref": len(imported), "not_verified": unverified,
                                                                                         "verified_at": ver["verified_at"], "source": ver["source"]},
                "corpus": {"sources": len(imported), "full_text": 0, "tier": "METADATA_ONLY"}, "review_protocol": {"status": audit["protocol_status"], "drafted_by": audit.get("drafted_by"), "adopted": False,
                                                                                                                 "components": {k: v["status"] for k, v in audit["components"].items()}},
                "readiness": audit["readiness"], "origins": {"PRODUCTION_EXPORTER": "written by research export review / research export retrieval from the store",
                                                             "SCRIPT_FROM_STORE": "generated by the pilot script from the store and seeds_verified.json",
                                                             "ASSISTANT_AUTHORED": "text written by the assistant"},
                "files": [{"path": n, "sha256": sha256(d), "bytes": len(d), "origin": o} for n, (d, o) in sorted(files.items())],
                "exports": {"review_export": {"manifest_sha256": exp_manifests["review_export"]}, "retrieval_export": {"manifest_sha256": exp_manifests["retrieval_export"]}},
                "not_done": ["no full text imported", "no model call", "no screening decision", "no database search", "no protocol adopted", "no landscape, assumption map or tension inspection"],
                "blockers": blockers}
    (PILOT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"problem": pid, "retrieval": rid, "imported": len(imported), "not_verified": unverified, "protocol": audit["protocol_status"], "ready": audit["readiness"]["can_build_landscape"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
