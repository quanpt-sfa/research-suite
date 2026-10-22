"""WP6.2: the command line, compatibility with everything that existed before, and three independent designs sharing one Research Core. Synthetic: `SCHEMA_TESTED`."""
from __future__ import annotations

import json

import pytest

from research_harness import cli_knowledge as K
from research_harness.cli import main
from research_harness.discovery import landscape as L
from research_harness.knowledge import claim_relations as CR
from research_harness.knowledge import claims as CL
from research_harness.knowledge import corpus
from research_harness.workflows import eca
from research_harness.workflows import reading_protocols as rp
from research_harness.workflows.domain_engine import list_builtin_specs
from tests import claims_fixtures as F
from tests import landscape_fixture as LF


@pytest.fixture()
def w(tmp_path):
    return F.make_store(tmp_path)


def cid(v, fragment):
    return next(c["id"] for c in v["claims"] if c.get("wording") and fragment in c["wording"]["quote"])


# ------------------------------------------------------------------------------------------------ the command line

def test_the_claims_commands_run_end_to_end_and_say_what_is_only_a_proposal(w, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(K, "_adapter", lambda args: F.reader())
    tf = tmp_path / "token"
    tf.write_text(w["token"], encoding="utf-8")
    base = ["--project", "PRJ_CLAIMS", "--home", str(w["home"])]
    auth = [*base, "--token-file", str(tf)]

    def j(*argv, rc=0):
        assert main([*map(str, argv), "--json"]) == rc, capsys.readouterr().err
        return json.loads(capsys.readouterr().out)

    out = j("claims", "build", w["ids"]["cr"], w["ids"]["pre"], w["ids"]["jour"], "--label", "cli test", *auth)
    inv = out["inventory_id"]
    assert out["claims"] > 10 and out["calls"] >= 1
    v = j("claims", "show", inv, *base)
    a, e = cid(v, "We argue that communicative"), next(c["id"] for c in v["claims"] if c["source_id"] == w["ids"]["pre"] and "investment of the self" in c["wording"]["quote"])
    assert main(["claims", "show", inv, *base]) == 0
    shown = capsys.readouterr().out
    assert "AUTHOR_REPORTS_PRIOR_WORK" in shown and "wording:" in shown and "a count of claims is not evidence" in shown
    assert j("claims", "audit", inv, *base)["flags"] == []
    j("claims", "review", inv, cid(v, "Previous studies"), "CORRECTED", "--reason", "describes earlier studies", "--set", "attribution=AUTHOR_REPORTS_PRIOR_WORK", *auth)
    rel = j("claims", "relation-add", inv, "--proposal", json.dumps({"kind": "COMPLEMENTS", "from": a, "to": e, "rationale": "two ways of separating state from behaviour"}), *auth)
    assert rel["relation"] == "R001"
    shown = main(["claims", "show", inv, *base]) == 0 and capsys.readouterr().out
    assert "R001 " in shown and "UNCONFIRMED" in shown and "proposals; none is established until you decide" in shown
    j("claims", "relation-review", inv, "R001", "CONFIRMED", "--reason", "I read both", *auth)
    assert j("claims", "show", inv, *base)["relations"][0]["confirmation"]["state"] == "CONFIRMED_BY_RESEARCHER"
    p = next(c["id"] for c in v["claims"] if c["wording"]["quote"] == F.V["w_pre"])
    jn = next(c["id"] for c in v["claims"] if c["wording"]["quote"] == F.V["w_post"])
    j("claims", "align-add", inv, "--proposal", json.dumps({"change": "WEAKENED", "earlier": p, "later": jn, "what_changed": "always became can", "rationale": "r"}), *auth)
    assert j("claims", "candidates", inv, a, *base)["candidates"]
    form = tmp_path / "form.json"
    j("claims", "annotation-form", w["ids"]["cr"], "--annotator", "annotator_a", "--out", form, *base)
    assert json.loads(form.read_text(encoding="utf-8"))["status"] == "BLANK"
    exp = tmp_path / "exp"
    j("export", "claims", inv, "--out", exp, *base)
    assert (exp / "claim_inventory.csv").is_file() and (exp / "manifest.json").is_file()
    assert main(["export", "verify", str(exp), *base]) == 0 and capsys.readouterr().out.strip() == "VERIFIED"
    assert j("claims", "list", *base)["inventories"][0]["inventory_id"] == inv


def test_the_commands_refuse_what_the_library_refuses_and_say_why(w, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(K, "_adapter", lambda args: F.reader())
    tf = tmp_path / "token"
    tf.write_text(w["token"], encoding="utf-8")
    auth = ["--project", "PRJ_CLAIMS", "--home", str(w["home"]), "--token-file", str(tf)]
    abstract_only = corpus.import_metadata_only(w["store"], w["token"], citation_key="meta", title="Only metadata")["object_id"]
    assert main(["claims", "build", abstract_only, *auth]) == 1
    assert "SOURCE_HAS_NO_FULL_TEXT" in capsys.readouterr().err
    inv = CL.build(w["store"], w["token"], [w["ids"]["cr"]], F.reader())["inventory_id"]
    assert main(["claims", "review", inv, "C001", "CORRECTED", "--reason", "x", *auth]) == 1                 # CORRECTED needs something corrected
    assert "CORRECTION_NOT_ALLOWED" in capsys.readouterr().err
    assert main(["claims", "show", "CINV_999999", *auth[:4]]) == 1 and "INVENTORY_NOT_FOUND" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------ compatibility

def test_an_existing_landscape_builds_and_reads_the_same_with_an_inventory_beside_it_and_an_empty_project_has_none(tmp_path):
    lw = LF.build_world(tmp_path)
    assert CL.list_inventories(lw["store"]) == []
    lid = L.build(lw["store"], lw["token"], lw["rid"], LF.reader())["landscape_id"]
    before = L.view(lw["store"], lid)
    ids = [lw["ids"]["pat"], lw["ids"]["quinn"]]
    script = {"pat": {"RP_CLAIMS": [F.rec("empirical_finding", LF.S["pat_find"], F.OWN, direction="negative")]}, "quinn": {"RP_CLAIMS": [F.rec("empirical_finding", LF.S["q_find"], F.OWN, direction="positive")]}}
    inv = CL.build(lw["store"], lw["token"], ids, F.reader(script))["inventory_id"]
    after = L.view(lw["store"], lid)
    assert [e["id"] for e in before["entries"]] == [e["id"] for e in after["entries"]] and before["inferences"] == after["inferences"] and before["coverage"] == after["coverage"]
    assert len(CL.view(lw["store"], inv)["claims"]) == 2 and CL.list_inventories(lw["store"])[0]["inventory_id"] == inv
    kinds = {s["object_id"] for s in eca.iter_statements(lw["store"])}
    assert lid in kinds and inv in kinds                                                               # both are statements ECA can be asked about; neither replaced the other
    with lw["store"].read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM object_heads WHERE object_type='Landscape'").fetchone()[0] == 1


def test_the_older_protocols_and_their_cache_keys_behave_exactly_as_before(w):
    key_before = rp.extraction_key("SRC_X", 3, "a" * 64, rp.get_protocol("RP_LANDSCAPE"))
    assert key_before == f"SRC_X@3#{'a' * 16}|RP_LANDSCAPE@2"                                              # no model, no configuration in the key of an older protocol
    with pytest.raises(Exception) as e:
        rp.extraction_key("SRC_X", 3, "a" * 64, rp.get_protocol("RP_CLAIMS"))
    assert "model-scoped" in str(e.value)
    assert not rp.get_protocol("RP_LANDSCAPE").model_scoped and not rp.get_protocol("RP_THEORY").all_sections
    # the older validation path is unchanged: no pieces, no attributes, and a stray `attributes` in an answer to an older protocol is ignored, not stored
    proto = rp.get_protocol("RP_THEORY")
    text = F.CR["def1"]
    ok, rej, _, err = rp.validate_extraction(proto, {"records": [{"field": "construct_definition", "value": "v", "quote": text, "attributes": {"scope": {"setting": "x"}}}]}, text, 0, text)
    assert err is None and not rej and "attributes" not in ok[0] and ok[0]["char_start"] == 0


def test_an_inventory_rests_on_its_sources_and_passages_by_edges_so_a_library_closure_would_carry_them(w):
    inv = CL.build(w["store"], w["token"], [w["ids"]["cr"]], F.reader())["inventory_id"]
    with w["store"].read() as conn:
        edges = conn.execute("SELECT relation, target_id, target_revision FROM edges WHERE source_id=?", (inv,)).fetchall()
    targets = {(r[1], r[2]) for r in edges}
    v = CL.view(w["store"], inv)
    assert (w["ids"]["cr"], 1) in targets and all(r[0] == "derived_from" for r in edges)
    for c in v["claims"]:
        for ln in c["evidence"]:
            assert (ln["passage_id"], ln["passage_revision"]) in targets
    assert len(targets) == 1 + len({ln["passage_id"] for c in v["claims"] for ln in c["evidence"]})


def test_claims_of_an_inventory_are_statements_eca_can_be_asked_about_and_the_sampling_counts_them(w):
    inv = CL.build(w["store"], w["token"], [w["ids"]["cr"]], F.reader())["inventory_id"]
    v = CL.view(w["store"], inv)
    mine = [s for s in eca.iter_statements(w["store"]) if s["object_id"] == inv]
    assert len(mine) == len(v["claims"]) and {s["stratum"] for s in mine} >= {"ClaimInventory:theoretical_proposition", "ClaimInventory:claimed_contribution"}
    assert next(s for s in mine if s["element"] == cid(v, "This paper contributes"))["risk_tier"] == "HIGH"
    st = eca.resolve_statement(w["store"], inv, element=cid(v, "We define communicative"))
    assert st.object_id == inv and st.risk_tier == "STANDARD" and st.text.startswith("Participation is contributing")


def test_a_source_revised_after_the_build_marks_the_inventory_stale_without_touching_the_claims(w):
    inv = CL.build(w["store"], w["token"], [w["ids"]["cr"]], F.reader())["inventory_id"]
    n_rev = w["store"].get_object(inv)["revision"]
    corpus.import_source(w["store"], w["token"], citation_key="cr", title="Participation Without Engagement", year=2024, text=F.CR_TEXT + "\nAn erratum.\n")
    v = CL.view(w["store"], inv)
    assert v["currency"]["sources"]["status"] == "STALE" and w["store"].get_object(inv)["revision"] == n_rev
    assert {c["currency"]["status"] for c in v["claims"]} == {"SOURCE_REVISED"} and all(c["wording"]["quote"] in F.CR_TEXT for c in v["claims"])       # what was read stays what was read


# ------------------------------------------------------------------------------------------------ three designs, one Research Core

def test_three_independent_designs_share_one_core_and_none_borrows_anothers_rules(w):
    ids = w["ids"]
    invs = {k: CL.build(w["store"], w["token"], [ids[k]], F.reader())["inventory_id"] for k in ("cr", "edu", "fraud")}
    views = {k: CL.view(w["store"], i) for k, i in invs.items()}
    with w["store"].read() as conn:
        types = {r[0] for r in conn.execute("SELECT DISTINCT object_type FROM object_heads").fetchall()}
    assert types == {"Source", "EvidencePassage", "ClaimInventory"}                                       # no Dataset, Analysis or Result was needed for any of the three
    roles = {k: {c["role"] for c in v["claims"]} for k, v in views.items()}
    assert "THEORETICAL_PROPOSITION" in roles["cr"] and "INTERPRETIVE_FINDING" in roles["edu"] and "EMPIRICAL_FINDING" in roles["fraud"] and "INTERPRETIVE_FINDING" not in roles["fraud"]
    # critical review: reported and critiqued prior work is kept apart from the authors' own claims
    assert {c["attribution"]["value"] for c in views["cr"]["claims"]} == {F.OWN, F.REP, F.CRIT}
    # education: the completion claim and the limitation that completion does not show learning are two claims, and the limitation can qualify the finding without rewriting it
    done, lim = cid(views["edu"], F.EDU["done"][:30]), cid(views["edu"], "Completing a dashboard")
    CR.add_relation(w["store"], w["token"], invs["edu"], {"kind": "QUALIFIES", "from": lim, "to": done, "rationale": "completion is not evidence of the ability to judge correctness"})
    after = CL.view(w["store"], invs["edu"])
    assert next(c for c in after["claims"] if c["id"] == done)["text"] == "Every student produced a complete dashboard." and after["relations"][0]["confirmation"]["state"] == "UNCONFIRMED"
    # fraud detection: a prediction score and the limitation that it ranks labels rather than identifying fraud, plus label provenance and the temporal split as methodological claims
    pred, ident = cid(views["fraud"], "average precision of 0.41"), cid(views["fraud"], "do not show that the model identifies fraud")
    CR.add_relation(w["store"], w["token"], invs["fraud"], {"kind": "QUALIFIES", "from": ident, "to": pred, "rationale": "a ranking of restatement labels is not an identification of fraud"})
    f = CL.view(w["store"], invs["fraud"])
    meth = [c for c in f["claims"] if c["role"] == "METHODOLOGICAL_CLAIM"]
    assert {c["constructs"][0] for c in meth} == {"label validity", "temporal split"} and next(c for c in meth if c["constructs"] == ["temporal split"])["scope"]["period"]["basis"] == "STATED_IN_QUOTE"
    # the gate for one design does not treat another's claims as a contradiction
    fake = CL.view(w["store"], invs["cr"])
    with pytest.raises(Exception) as err:
        CR.add_relation(w["store"], w["token"], invs["cr"], {"kind": "DEFINES", "from": cid(fake, "We argue that communicative"), "to": cid(fake, "We assume that recipients"), "rationale": "x"})
    assert "FROM_NOT_A_DEFINITION" in str(err.value)


def test_the_workflow_contracts_of_wp61_are_still_there_and_unaffected(w):
    assert {"LIB_DW_CRITICAL_SYNTHESIS", "LIB_DW_CONCEPTUAL_ARGUMENT", "LIB_DW_QUALITATIVE_INQUIRY", "LIB_DW_ACC_FRAUD_DETECTION", "LIB_DW_GENERIC"} <= set(list_builtin_specs())
    CL.build(w["store"], w["token"], [w["ids"]["cr"]], F.reader())
    assert w["store"].get_object(w["ids"]["cr"])["revision"] == 1                                         # reading for claims never revises a Source


def test_a_landscape_says_which_fields_reached_their_cap_so_a_record_count_is_not_read_as_a_finding_count(tmp_path):
    from research_harness.discovery import retrieval as RT
    from research_harness.export.landscape import build_landscape_export
    lw = LF.build_world(tmp_path, search=False, make_run=False)
    sentences = [f"Detection models change earnings manipulation measure number {i} among the invented firms." for i in range(9)]
    corpus.import_source(lw["store"], lw["token"], citation_key="nine", title="Nine Findings on Detection Models", year=2025, text=F.paper("Nine Findings on Detection Models", "Detection models and earnings manipulation.", [("1 Results", sentences)]))
    rid = RT.new_run(lw["store"], lw["token"], lw["pid"], terms=LF.TERMS, top_n=50)["retrieval_id"]
    RT.search(lw["store"], lw["token"], rid)
    records = {**LF.RECORDS, "nine": {"RP_LANDSCAPE": [{"field": "finding", "value": s, "quote": s, "label": f"rel{i}", "type": "positive"} for i, s in enumerate(sentences)], "RP_THEORY": []}}
    lid = L.build(lw["store"], lw["token"], rid, LF.reader(records=records))["landscape_id"]
    v = L.view(lw["store"], lid)
    assert v["coverage"]["fields_at_cap"] == {"SRC_NINE": ["RP_LANDSCAPE@2.finding"]}
    assert sum(1 for e in v["entries"] if e["source_id"] == "SRC_NINE" and e["category"] == "finding") == 8
    text = build_landscape_export(lw["store"], lid).files["research_landscape.md"].decode("utf-8")
    assert "Fields at their cap" in text and "SRC_NINE" in text and "the number of entries is not the number of claims" in text
