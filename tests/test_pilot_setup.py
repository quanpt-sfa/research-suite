"""The critical-review pilot setup: reproducible offline from the verified seed file, honest about what it did not do, and no DOI that Crossref did not return. Synthetic acceptance of the setup only."""
from __future__ import annotations

import csv
import importlib.util
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "pilots" / "ai_communication_critical_review"
DOI_RE = re.compile(r"10\.\d{4,9}/\S")


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "pilot" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def unknown_dois(text: str, allowed: set[str]) -> list[str]:
    """DOI-looking strings left in the text once every DOI that Crossref returned has been removed (some DOIs contain brackets and semicolons, so they are removed whole, not parsed)."""
    rest = text.lower()
    for d in sorted(allowed, key=len, reverse=True):
        rest = rest.replace(d, " ")
    return DOI_RE.findall(rest)


def verified_dois() -> set[str]:
    """Every DOI that Crossref itself returned in the verification: the matched records, the nearest candidates listed for a human, and the correction or relation notices of a record."""
    out: set[str] = set()
    for r in json.loads((PILOT / "seeds_verified.json").read_text(encoding="utf-8"))["results"]:
        out.update(x["doi"].lower() for x in [r, *r.get("nearest", [])] if x.get("doi"))
        out.update(n.split()[-1].lower() for n in (r.get("publication") or {}).get("notes", []))
    return out


def test_the_matching_rule_accepts_only_title_author_and_year_together_and_never_fills_in_from_memory():
    v = load("verify_seeds")
    seed = {"title": "Social exchange theory: An interdisciplinary review", "first_author": "Cropanzano", "year": 2005}
    item = {"DOI": "10.1177/0149206305279602", "title": ["Social Exchange Theory: An Interdisciplinary Review"], "author": [{"family": "Cropanzano", "given": "Russell"}], "issued": {"date-parts": [[2005, 12]]}}
    got, near = v.match(seed, [item])
    assert got is item and near[0]["similarity"] == 1.0
    for bad in ({**item, "author": [{"family": "Someone", "given": "Else"}]}, {**item, "issued": {"date-parts": [[1999]]}}, {**item, "title": ["A quite different title about exchange"]}):
        assert v.match(seed, [bad])[0] is None
    assert v.full_title({"title": ["AI-Mediated Communication"], "subtitle": ["How the Perception Affects Trust"]}) == "AI-Mediated Communication: How the Perception Affects Trust"
    status = v.publication_status({"type": "journal-article", "updated-by": [{"type": "erratum", "DOI": "10.1/x"}]})
    assert status["retraction_or_correction_noticed"] is True and "peer review is not asserted" in status["label"]
    assert "preprint" in v.publication_status({"type": "posted-content"})["label"]


def test_the_committed_verification_is_internally_consistent_and_every_unverified_seed_is_listed_as_such():
    seeds = json.loads((PILOT / "seeds.json").read_text(encoding="utf-8"))
    ver = json.loads((PILOT / "seeds_verified.json").read_text(encoding="utf-8"))
    assert [s["key"] for s in seeds["seeds"]] == [r["key"] for r in ver["results"]]
    for r in ver["results"]:
        assert (r["status"] == "VERIFIED_CROSSREF") == bool(r.get("doi"))
        if r["status"] == "VERIFIED_CROSSREF":
            assert r["query_url"].startswith("https://api.crossref.org/works?") and r["publication"]["crossref_type"]
    assert ver["summary"]["verified"] + ver["summary"]["not_verified"] == len(seeds["seeds"])
    assert "NOT the result of a systematic search" in seeds["about"] and "NOT a screening decision" in seeds["about"]


def test_the_pilot_setup_rebuilds_offline_and_claims_nothing_it_did_not_do(tmp_path):
    for name in ("seeds.json", "seeds_verified.json"):
        shutil.copy(PILOT / name, tmp_path / name)
    b = load("build_ai_communication_pilot")
    b.PILOT, b.STORE_HOME = tmp_path, tmp_path / "_store" / "home"
    assert b.main() == 0
    m = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert m["status"] == "REAL_CORPUS_VALIDATION_PENDING" and m["corpus"] == {"sources": m["seeds"]["verified_against_crossref"], "full_text": 0, "tier": "METADATA_ONLY"}
    assert m["review_protocol"]["status"] == "DRAFT" and m["review_protocol"]["adopted"] is False and m["review_protocol"]["components"]["appraisal"] == "NOT_DECLARED"
    assert m["review_protocol"]["components"]["screening"] == "INCOMPLETE" and m["readiness"]["can_build_landscape"] is False
    assert set(m["not_done"]) >= {"no full text imported", "no model call", "no screening decision", "no database search", "no protocol adopted"}
    required = {"problem_framing.md", "search_protocol.md", "corpus_inventory.csv", "conceptual_map.md", "theoretical_assumptions.md", "tension_inspection_report.md", "citation_evidence_audit.md",
                "limitations.md"}
    assert required <= {f["path"] for f in m["files"]} and all((tmp_path / f["path"]).is_file() for f in m["files"])
    origins = {f["path"]: f["origin"] for f in m["files"]}
    assert origins["search_protocol.md"].startswith("PRODUCTION_EXPORTER") and origins["corpus_inventory.csv"].startswith("PRODUCTION_EXPORTER") and origins["limitations.md"] == "ASSISTANT_AUTHORED"
    for n in ("conceptual_map.md", "theoretical_assumptions.md", "tension_inspection_report.md"):
        text = (tmp_path / n).read_text(encoding="utf-8")
        assert "NOT_AVAILABLE" in text and "REAL_CORPUS_VALIDATION_PENDING" in text and "contains no analysis" in text           # no content is made up where none can exist
    with (tmp_path / "corpus_inventory.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == m["corpus"]["sources"] and {r["tier"] for r in rows} == {"METADATA_ONLY"} and {r["full_text"] for r in rows} == {"False"}
    assert {r["screening_decision"] for r in rows} == {""}                # nothing was screened in or out by the assistant
    # no DOI appears that Crossref did not return, and no author is quoted
    allowed = verified_dois()
    for p in tmp_path.rglob("*"):
        if p.is_file() and "_store" not in p.parts and p.suffix in (".md", ".csv", ".json") and p.name not in ("seeds.json",):
            assert unknown_dois(p.read_text(encoding="utf-8"), allowed) == [], p.name
    assert "No quotation of an author appears anywhere" in (tmp_path / "limitations.md").read_text(encoding="utf-8")


def test_the_committed_pilot_directory_matches_the_verified_file_and_holds_no_store():
    assert not any(p.name in ("state.sqlite3", "token") for p in PILOT.rglob("*") if "_store" not in p.parts)
    allowed = verified_dois()
    for p in PILOT.rglob("*"):
        if p.is_file() and "_store" not in p.parts and p.suffix in (".md", ".csv") and p.parent == PILOT:
            assert unknown_dois(p.read_text(encoding="utf-8"), allowed) == [], p.name
