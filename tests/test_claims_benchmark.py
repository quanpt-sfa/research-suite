"""WP6.2: the benchmark harness. Hand-checked metrics, gold that cannot come from a model, and SciFact as a benchmark of one narrow component.

Everything computed on the invented gold fixture here tests the metric code. It says nothing about any model: the reader is scripted, and its mistakes were planted so that the metrics have
something to find. The real-data check (SciFact dev) runs only when the licensed archive has been downloaded by whoever runs the tests."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from research_harness.benchmarks import claims as BM
from research_harness.benchmarks import scifact as SF
from research_harness.knowledge import claim_relations as CR
from research_harness.knowledge import claims as CL
from research_harness.knowledge.common import KnowledgeError
from tests import claims_fixtures as F


def span(start, end, text=None):
    return {"start": start, "end": end, **({"text": text} if text else {})}


def code(fn, *a, **k):
    with pytest.raises(KnowledgeError) as e:
        fn(*a, **k)
    return e.value.code


# ------------------------------------------------------------------------------------------------ the arithmetic

def test_precision_recall_and_f1_are_the_textbook_values_and_undefined_when_there_is_nothing_to_divide():
    r = BM.prf(3, 1, 2)
    assert (r["precision"], r["recall"], r["f1"]) == (0.75, 0.6, 0.6667)
    assert BM.prf(0, 0, 0) == {"tp": 0, "fp": 0, "fn": 0, "precision": None, "recall": None, "f1": None}
    assert BM.prf(0, 2, 2)["f1"] == 0.0


def test_exact_overlap_and_lexical_matching_are_different_things_and_are_never_mixed():
    pred = [span(0, 10), span(20, 30), span(50, 60)]
    gold = [span(0, 10), span(22, 32), span(100, 110)]
    ex, ov, strict = BM.match(pred, gold, "exact"), BM.match(pred, gold, "overlap", 0.5), BM.match(pred, gold, "overlap", 0.8)
    assert (ex["tp"], ex["fp"], ex["fn"]) == (1, 2, 2) and (ov["tp"], ov["fp"], ov["fn"]) == (2, 1, 1) and (strict["tp"], strict["fp"], strict["fn"]) == (1, 2, 2)
    # the same words in a different place: no span match, a lexical match
    a, b = [span(0, 20, "participation is not engagement")], [span(500, 520, "engagement is not participation")]
    assert BM.match(a, b, "overlap")["tp"] == 0 and BM.match(a, b, "lexical")["tp"] == 1 and BM.match(a, b, "lexical")["mode"] == "lexical"
    assert code(BM.match, a, b, "semantic") == "MODE_UNKNOWN"                       # no embedding judgement exists here, and none is pretended


def test_matching_is_one_to_one_best_first_and_reproducible():
    pred = [span(0, 10), span(2, 12)]
    gold = [span(1, 11)]
    m = BM.match(pred, gold, "overlap", 0.5)
    assert m["tp"] == 1 and m["fp"] == 1 and m["pairs"] == [(0, 0, pytest.approx(9 / 11))] or m["pairs"][0][0] in (0, 1)
    assert BM.match(pred, gold, "overlap", 0.5) == m


def test_cohens_kappa_is_the_hand_computed_value():
    assert BM.cohen_kappa(["A", "A", "B", "B"], ["A", "B", "B", "B"]) == 0.5
    assert BM.cohen_kappa(["A", "A"], ["A", "A"]) is None                                  # no variation: kappa is undefined


# ------------------------------------------------------------------------------------------------ gold: independent, human, or refused

def gold_dict(**over):
    g = {"gold_version": "1", "source": {"source_id": "S"}, "annotation": {"annotators": ["annotator_a"], "independent_of_system_output": True, "kind": "SYNTHETIC_FIXTURE"},
         "claims": [{"id": "G1", "start": 0, "end": 10, "attribution": "AUTHOR_OWN_CLAIM", "role": "EMPIRICAL_FINDING"}]}
    g.update(over)
    return g


def test_gold_that_could_come_from_a_model_or_from_nobody_is_refused():
    assert BM.load_gold(gold_dict())["kind"] == "SYNTHETIC_FIXTURE"
    assert code(BM.load_gold, gold_dict(annotation={"annotators": ["a"], "independent_of_system_output": False})) == "GOLD_NOT_INDEPENDENT"
    assert code(BM.load_gold, gold_dict(annotation={"annotators": ["a"]})) == "GOLD_NOT_INDEPENDENT"
    assert code(BM.load_gold, gold_dict(annotation={"annotators": [], "independent_of_system_output": True})) == "GOLD_NO_ANNOTATORS"
    assert code(BM.load_gold, gold_dict(annotation={"annotators": ["model:scripted-claims-reader"], "independent_of_system_output": True})) == "GOLD_FROM_MODEL_OUTPUT"
    assert code(BM.load_gold, gold_dict(claims=[{"id": "G1", "start": 0, "end": 10, "generated_by": "model:x"}])) == "GOLD_FROM_MODEL_OUTPUT"
    assert code(BM.load_gold, gold_dict(claims=[{"id": "G1", "start": 5, "end": 5}])) == "GOLD_SPAN_INVALID"
    assert code(BM.load_gold, gold_dict(claims=[{"id": "G1", "start": 0, "end": 5, "attribution": "AUTHOR_VERIFIED_FACT"}])) == "GOLD_LABEL_UNKNOWN"


def test_the_blank_form_is_blank_asks_for_independence_and_holds_no_system_output(tmp_path):
    w = F.make_store(tmp_path)
    obj, text = __import__("research_harness.knowledge.corpus", fromlist=["x"]).source_text(w["store"], w["ids"]["cr"])
    from research_harness.knowledge import corpus
    form = BM.blank_form(w["ids"]["cr"], obj["content"]["text_sha256"], corpus.source_sections(w["store"], obj, text), annotator="annotator_a")
    assert form["status"] == "BLANK" and form["claims"] == [] and form["relations"] == [] and form["alignments"] == []
    assert form["annotation"]["independent_of_system_output"] is None and form["annotation"]["annotators"] == ["annotator_a"]
    assert set(form["vocabularies"]["claim_attribution"]) >= {"AUTHOR_OWN_CLAIM", "AUTHOR_REPORTS_PRIOR_WORK"} and form["sections"]
    assert "Alder" not in json.dumps(form)                                              # the form carries headings and offsets, never a claim
    assert code(BM.load_gold, form) == "GOLD_NOT_INDEPENDENT"                            # a blank form is not gold until a person has filled it and attested independence


def test_two_annotators_agree_and_differ_and_only_an_adjudicators_reasoned_decision_resolves_a_difference():
    a = gold_dict(claims=[{"id": "A1", "start": 0, "end": 10, "attribution": "AUTHOR_OWN_CLAIM", "role": "EMPIRICAL_FINDING"},
                          {"id": "A2", "start": 20, "end": 30, "attribution": "AUTHOR_REPORTS_PRIOR_WORK", "role": "THEORETICAL_PROPOSITION"},
                          {"id": "A3", "start": 40, "end": 50, "attribution": "AUTHOR_OWN_CLAIM", "role": "LIMITATION"}])
    b = gold_dict(annotation={"annotators": ["annotator_b"], "independent_of_system_output": True},
                  claims=[{"id": "B1", "start": 0, "end": 10, "attribution": "AUTHOR_OWN_CLAIM", "role": "EMPIRICAL_FINDING"},
                          {"id": "B2", "start": 20, "end": 30, "attribution": "AUTHOR_OWN_CLAIM", "role": "THEORETICAL_PROPOSITION"},
                          {"id": "B4", "start": 70, "end": 80, "attribution": "AUTHOR_OWN_CLAIM", "role": "DEFINITION"}])
    cmp = BM.compare_annotators(a, b)
    assert cmp["claim_agreement"]["tp"] == 2 and cmp["claim_agreement"]["fp"] == 1 and cmp["claim_agreement"]["fn"] == 1
    kinds = sorted(d["kind"] for d in cmp["disagreements"])
    assert kinds == ["attribution_differs", "only_annotator_a", "only_annotator_b"] and cmp["attribution_kappa"] == 0.0
    undecided = BM.adjudicate(a, b, [], adjudicator="adjudicator_c")
    assert len(undecided["annotation"]["unresolved"]) == 3 and [c["id"] for c in undecided["claims"]] == ["A1", "A2"] and undecided["claims"][1]["attribution"] is None
    decided = BM.adjudicate(a, b, [{"kind": "attribution_differs", "a": "A2", "b": "B2", "take": "a", "rationale": "the sentence describes earlier work"},
                                   {"kind": "only_annotator_a", "a": "A3", "b": None, "take": "a", "rationale": "it is a stated limitation"},
                                   {"kind": "only_annotator_b", "a": None, "b": "B4", "take": "neither", "rationale": "a heading, not a claim"}], adjudicator="adjudicator_c")
    assert decided["annotation"]["unresolved"] == [] and sorted(c["id"] for c in decided["claims"]) == ["A1", "A2", "A3"]
    assert next(c for c in decided["claims"] if c["id"] == "A2")["attribution"] == "AUTHOR_REPORTS_PRIOR_WORK" and decided["annotation"]["adjudicator"] == "adjudicator_c"
    assert BM.load_gold(decided)["claims"]
    assert code(BM.adjudicate, a, b, [], adjudicator="model:x") == "ADJUDICATOR_REQUIRED"
    no_reason = BM.adjudicate(a, b, [{"kind": "attribution_differs", "a": "A2", "b": "B2", "take": "a", "rationale": " "}], adjudicator="adjudicator_c")
    assert any(u["kind"] == "attribution_differs" for u in no_reason["annotation"]["unresolved"])


# ------------------------------------------------------------------------------------------------ the metrics on a system that makes known mistakes

def locate(text, sentence):
    s = text.index(sentence)
    return s, s + len(sentence)


def gold_cr():
    t = F.CR_TEXT

    def c(gid, frag, attribution, role, **kw):
        s, e = locate(t, F.CR[frag])
        return {"id": gid, "start": s, "end": e, "attribution": attribution, "role": role, **kw}
    ev = lambda frag, role: {**dict(zip(("start", "end"), locate(t, F.CR[frag]))), "role": role}          # noqa: E731
    claims = [c("cr:G01", "rep1", F.REP, "THEORETICAL_PROPOSITION"), c("cr:G02", "rep2", F.REP, "EMPIRICAL_FINDING"), c("cr:G03", "crit1", F.CRIT, "THEORETICAL_PROPOSITION"),
              c("cr:G04", "own1", F.OWN, "THEORETICAL_PROPOSITION", evidence=[ev("own1_q", "QUALIFIER"), ev("evid1", "SUPPORTING_EVIDENCE")], qualifiers=[ev("own1_q", "QUALIFIER")]),
              c("cr:G05", "gap1", F.OWN, "GAP_STATEMENT"), c("cr:G06", "def1", F.OWN, "DEFINITION"), c("cr:G07", "assume1", F.OWN, "ASSUMPTION"),
              c("cr:G08", "comp1", F.OWN, "THEORETICAL_PROPOSITION", components=["participation raises visibility", "visibility raises attention to the sender"]),
              c("cr:G09", "contr1", F.OWN, "CLAIMED_CONTRIBUTION"), c("cr:G10", "lim1", F.OWN, "LIMITATION"), c("cr:G11", "evid1", F.OWN, "EMPIRICAL_FINDING")]
    return BM.load_gold({"gold_version": "1", "source": {"source_id": "cr"}, "annotation": {"annotators": ["annotator_a"], "independent_of_system_output": True, "kind": "SYNTHETIC_FIXTURE"},
                         "claims": claims, "relations": [{"kind": "SUPPORTS", "from": "cr:G06", "to": "cr:G04"}, {"kind": "QUALIFIES", "from": "cr:G08", "to": "cr:G04"}], "alignments": []})


def system_with_planted_mistakes(tmp_path):
    w = F.make_store(tmp_path)
    recs = F.cr_records()
    recs[1] = F.rec("empirical_finding", F.CR["rep2"], F.OWN, direction="positive")                    # planted: a reported claim labelled the authors' own
    inv = CL.build(w["store"], w["token"], [w["ids"]["cr"]], F.reader({"cr": {"RP_CLAIMS": recs}}))["inventory_id"]          # planted: the last gold claim (evid1) is not found
    v = CL.view(w["store"], inv)
    cid = lambda f: next(c["id"] for c in v["claims"] if f in c["wording"]["quote"])           # noqa: E731
    CR.add_relation(w["store"], w["token"], inv, {"kind": "SUPPORTS", "from": cid("We define communicative"), "to": cid("We argue that communicative"), "rationale": "the proposition uses the definition"})
    CR.add_relation(w["store"], w["token"], inv, {"kind": "COMPLEMENTS", "from": cid("We argue that communicative"), "to": cid("No account yet"), "rationale": "belong together"})        # planted: not in gold
    return w, inv, CL.view(w["store"], inv)


def test_the_metrics_find_the_planted_mistakes_and_report_exactly_what_they_are(tmp_path):
    w, inv, v = system_with_planted_mistakes(tmp_path)
    gold = gold_cr()
    res = BM.evaluate(v, {w["ids"]["cr"]: gold}, {"kind": "SYNTHETIC_FIXTURE", "relations": gold["relations"], "alignments": []})
    s = res["sources"][w["ids"]["cr"]]
    det = s["1_claim_detection"]
    assert (det["tp"], det["fp"], det["fn"], det["precision"], det["recall"], det["f1"]) == (10, 0, 1, 1.0, 0.9091, 0.9524) and det["mode"] == "overlap"
    assert BM.claim_detection(BM.claims_from_view(v, w["ids"]["cr"]), gold["claims"], mode="exact")["tp"] == 10                      # the wording spans are identical to the gold's
    att = s["3_attribution"]
    assert att["n_matched_and_labelled"] == 10 and att["accuracy"] == 0.9 and att["confusion_gold_by_predicted"][F.REP] == {F.REP: 1, F.OWN: 1} and att["gold_claims_not_found"] == 1
    assert s["2_claim_type_classification"]["accuracy"] == 1.0
    ev = s["4_evidence_span_identification"]
    assert (ev["evidence_spans"]["tp"], ev["evidence_spans"]["fp"], ev["evidence_spans"]["fn"]) == (1, 0, 1) and ev["multi_span_claims"] == 1
    assert ev["multi_span_evidence_coverage"] == 0.5 and ev["multi_span_claims_fully_covered"] == 0.0 and ev["qualifier_omission_rate"] == 0.0 and ev["qualifiers_in_gold"] == 1
    link = s["5_claim_evidence_linking"]
    assert (link["tp"], link["fp"], link["fn"]) == (1, 0, 1)
    assert s["7_claim_decomposition"]["tp"] == 2 and s["7_claim_decomposition"]["mode"] == "lexical"
    assert s["unsupported_claim_rate"] == {"own_findings": 1, "with_nothing_beyond_wording": 1, "rate": 1.0}                 # the mislabelled reported finding now reads as an own finding with nothing behind it
    rel = res["8_cross_paper_relations"]
    assert (rel["tp"], rel["fp"], rel["fn"]) == (1, 1, 1)                                  # SUPPORTS found; COMPLEMENTS is not in gold; QUALIFIES is missed
    assert res["9_claim_version_alignment"] == {"gold_alignments": 0, "version_alignment_error": None, "confusion_gold_by_predicted": {}, "spurious_proposals": 0}
    assert res["10_recognition_of_addressed_tensions"]["status"] == "DATA_UNAVAILABLE" and res["no_composite_score"] is True and res["gold_kinds"] == ["SYNTHETIC_FIXTURE"]
    assert not any(k in json.dumps(res) for k in ('"composite_score"', '"overall_score"'))


def test_a_dismissed_relation_is_not_scored_and_a_missing_gold_layer_is_data_unavailable_not_zero(tmp_path):
    w, inv, v = system_with_planted_mistakes(tmp_path)
    CR.review_relation(w["store"], w["token"], inv, "R002", "DISMISSED", "on reflection they do not belong together")
    gold = gold_cr()
    res = BM.evaluate(CL.view(w["store"], inv), {w["ids"]["cr"]: gold}, {"kind": "SYNTHETIC_FIXTURE", "relations": gold["relations"], "alignments": []})
    assert (res["8_cross_paper_relations"]["tp"], res["8_cross_paper_relations"]["fp"]) == (1, 0)
    bare = {"gold_version": "1", "annotation": {"annotators": ["a"], "independent_of_system_output": True, "kind": "SYNTHETIC_FIXTURE"}, "claims": [{"id": "g", "start": 0, "end": 5}]}
    res2 = BM.evaluate(CL.view(w["store"], inv), {w["ids"]["cr"]: BM.load_gold(bare)})
    s = res2["sources"][w["ids"]["cr"]]
    assert s["3_attribution"] == {"status": "DATA_UNAVAILABLE"} and s["4_evidence_span_identification"] == {"status": "DATA_UNAVAILABLE"} and res2["8_cross_paper_relations"] == {"status": "DATA_UNAVAILABLE"}


def test_version_alignment_error_counts_wrong_and_missing_proposals_and_spurious_ones_apart(tmp_path):
    w = F.make_store(tmp_path)
    inv = CL.build(w["store"], w["token"], [w["ids"]["pre"], w["ids"]["jour"]], F.reader())["inventory_id"]
    v = CL.view(w["store"], inv)
    q = lambda quote: next(c["id"] for c in v["claims"] if c["wording"]["quote"] == quote)           # noqa: E731
    CR.add_alignment(w["store"], w["token"], inv, {"change": "WEAKENED", "earlier": q(F.V["w_pre"]), "later": q(F.V["w_post"]), "what_changed": "always became can", "rationale": "r"})
    CR.add_alignment(w["store"], w["token"], inv, {"change": "UNCHANGED", "earlier": q(F.V["r_pre"]), "later": q(F.V["r_post"]), "rationale": "r"})                  # planted: wrong, it reversed
    CR.add_alignment(w["store"], w["token"], inv, {"change": "UNCHANGED", "earlier": next(c["id"] for c in v["claims"] if c["source_id"] == w["ids"]["pre"] and "investment" in c["wording"]["quote"]),
                                                    "later": next(c["id"] for c in v["claims"] if c["source_id"] == w["ids"]["jour"] and "investment" in c["wording"]["quote"]), "rationale": "r"})   # planted: not in gold
    v = CL.view(w["store"], inv)

    def gold_for(sid, text, claims):
        out = []
        for gid, frag in claims:
            s_, e_ = locate(text, frag)
            out.append({"id": gid, "start": s_, "end": e_})
        return BM.load_gold({"gold_version": "1", "annotation": {"annotators": ["a"], "independent_of_system_output": True, "kind": "SYNTHETIC_FIXTURE"}, "claims": out})
    golds = {w["ids"]["pre"]: gold_for("pre", F.PRE_TEXT, [("p:w", F.V["w_pre"]), ("p:r", F.V["r_pre"]), ("p:q", F.V["q_pre"]), ("p:same", F.V["same"])]),
             w["ids"]["jour"]: gold_for("jour", F.JOURNAL_TEXT, [("j:w", F.V["w_post"]), ("j:r", F.V["r_post"]), ("j:q", F.V["q_post"]), ("j:same", F.V["same"])])}
    cross = {"kind": "SYNTHETIC_FIXTURE", "relations": [], "alignments": [{"earlier": "p:w", "later": "j:w", "change": "WEAKENED"}, {"earlier": "p:r", "later": "j:r", "change": "REVERSED"},
                                                                          {"earlier": "p:q", "later": "j:q", "change": "QUALIFIED"}]}
    res = BM.evaluate(v, golds, cross)
    al = res["9_claim_version_alignment"]
    assert al["gold_alignments"] == 3 and al["version_alignment_error"] == pytest.approx(2 / 3, abs=1e-3) and al["spurious_proposals"] == 1
    assert al["confusion_gold_by_predicted"] == {"WEAKENED": {"WEAKENED": 1}, "REVERSED": {"UNCHANGED": 1}, "QUALIFIED": {"None": 1}}


def test_cost_metrics_come_from_the_inventory_and_say_when_they_cannot_be_computed(tmp_path):
    w, inv, v = system_with_planted_mistakes(tmp_path)
    c = BM.cost_metrics(v)
    assert c["sources"] == 1 and c["model_calls"] >= 1 and c["tokens"] > 0 and c["tokens_per_claim"] == pytest.approx(c["tokens"] / c["claims"], abs=0.1)
    assert c["confirmed_claims"] == 0 and c["tokens_per_confirmed_claim"] is None and c["usage_source"] == "scripted" and c["cache_reuse_rate"] == 0.0
    u = BM.unsupported_claim_rate(v, w["ids"]["cr"])
    assert u["own_findings"] == 1 and u["rate"] == 1.0
    assert BM.unsupported_claim_rate(v, "SRC_NOT_THERE") == {"own_findings": 0, "with_nothing_beyond_wording": 0, "rate": None}


def test_the_benchmark_result_is_recorded_in_the_run_registry_with_its_artifact(tmp_path):
    w, inv, v = system_with_planted_mistakes(tmp_path)
    run = BM.record_results(w["store"], {"x": 1}, scope=inv, note="n")
    with w["store"].read() as conn:
        from research_harness.state import workflow_runs
        row = workflow_runs.get_run(conn, run)
    assert row["workflow"] == BM.BENCHMARK_WORKFLOW and row["artifact_sha256"] and json.loads(w["store"].artifacts.get_bytes(row["artifact_sha256"]))["results"] == {"x": 1}


# ------------------------------------------------------------------------------------------------ SciFact: format, licence, arithmetic

def tiny_scifact(tmp_path):
    d = tmp_path / "sf"
    d.mkdir(parents=True)
    corpus = [{"doc_id": 1, "title": "Invented title one", "abstract": ["Aspirin lowers fever in the invented trial.", "The invented trial enrolled adults.", "Fever fell by two degrees with aspirin."], "structured": False},
              {"doc_id": 2, "title": "Invented title two", "abstract": ["Statins reduce cholesterol.", "Cholesterol fell in all invented groups.", "Adverse events were rare."], "structured": False},
              {"doc_id": 3, "title": "Unrelated", "abstract": ["Weather patterns shift.", "Rain fell."], "structured": False}]
    claims = [{"id": 10, "claim": "Aspirin lowers fever.", "evidence": {"1": [{"label": "SUPPORT", "sentences": [0, 2]}]}, "cited_doc_ids": [1]},
              {"id": 11, "claim": "Statins reduce cholesterol levels.", "evidence": {"2": [{"label": "SUPPORT", "sentences": [0]}]}, "cited_doc_ids": [2]},
              {"id": 12, "claim": "A claim with no evidence about weather.", "evidence": {}, "cited_doc_ids": [3]}]
    (d / "corpus.jsonl").write_text("\n".join(json.dumps(x) for x in corpus), encoding="utf-8")
    (d / "claims_dev.jsonl").write_text("\n".join(json.dumps(x) for x in claims), encoding="utf-8")
    (d / "claims_test.jsonl").write_text("\n".join(json.dumps({"id": c["id"], "claim": c["claim"]}) for c in claims), encoding="utf-8")
    return d


def test_scifact_is_loaded_with_its_licence_and_only_a_labelled_split_can_be_scored(tmp_path):
    d = tiny_scifact(tmp_path)
    claims, corpus = SF.load(d, "dev")
    assert len(claims) == 3 and set(corpus) == {1, 2, 3}
    assert code(SF.load, d, "test") == "SPLIT_NOT_LABELLED"
    assert code(SF.load, tmp_path / "missing", "dev") == "DATA_UNAVAILABLE"
    assert SF.LICENCE["claims_and_annotations"] == "CC BY 4.0" and SF.LICENCE["abstracts"].startswith("ODC-By") and "not committed" in SF.LICENCE["redistribution"]


def test_a_corpus_that_contradicts_its_claims_is_refused(tmp_path):
    d = tiny_scifact(tmp_path)
    (d / "claims_dev.jsonl").write_text(json.dumps({"id": 1, "claim": "c", "evidence": {"1": [{"label": "SUPPORT", "sentences": [99]}]}, "cited_doc_ids": [1]}), encoding="utf-8")
    assert code(SF.load, d, "dev") == "DATA_INCONSISTENT"
    (d / "claims_dev.jsonl").write_text(json.dumps({"id": 1, "claim": "c", "evidence": {"7": [{"label": "SUPPORT", "sentences": [0]}]}, "cited_doc_ids": [7]}), encoding="utf-8")
    assert code(SF.load, d, "dev") == "DATA_INCONSISTENT"


def test_the_sentence_level_figures_are_the_hand_computed_ones_and_chance_is_reported_beside_them(tmp_path):
    claims, corpus = SF.load(tiny_scifact(tmp_path), "dev")
    r = SF.sentence_level(claims, corpus, ks=(1, 2))
    # claim 10 vs abstract 1: "Aspirin lowers fever." ranks sentence 0 first (all three words), sentence 2 ("Fever fell ... aspirin") second; rationale = {0, 2}
    # claim 11 vs abstract 2: sentence 0 ("Statins reduce cholesterol.") first; rationale = {0}
    assert r["claims_with_evidence"] == 2 and r["claim_abstract_pairs"] == 2 and r["rationale_sentences"] == 3
    assert r["at_k"]["1"]["any_rationale_in_top_k"] == 1.0 and r["at_k"]["1"]["rationale_sentence_recall"] == pytest.approx((0.5 + 1.0) / 2, abs=1e-4)
    assert r["at_k"]["2"]["rationale_sentence_recall"] == pytest.approx((1.0 + 1.0) / 2, abs=1e-4)
    # chance of at least one of the 2 rationale sentences in the top 1 of 3 is 2/3; of the 1 rationale sentence is 1/3
    assert r["at_k"]["1"]["chance_any_rationale_in_top_k"] == pytest.approx((2 / 3 + 1 / 3) / 2, abs=1e-4)
    assert SF.sentence_level(claims, corpus, ks=(1,), idf="corpus")["idf"] == "corpus"


def test_the_abstract_level_figures_rank_the_whole_corpus_and_chance_is_k_over_n(tmp_path):
    claims, corpus = SF.load(tiny_scifact(tmp_path), "dev")
    r = SF.abstract_level(claims, corpus, ks=(1, 2))
    assert r["claims_with_evidence"] == 2 and r["abstracts_ranked"] == 3
    assert r["at_k"]["1"]["any_cited_abstract_in_top_k"] == 1.0 and r["at_k"]["1"]["chance_any_cited_abstract_in_top_k"] == pytest.approx(1 / 3, abs=1e-4)
    assert r["at_k"]["2"]["chance_any_cited_abstract_in_top_k"] == pytest.approx(2 / 3, abs=1e-4)
    full = SF.evaluate(tiny_scifact(tmp_path / "again"), "dev")
    assert full["task_ids"] == [4, 5] and "claim extraction" in full["what_is_not"] and full["archive"]["sha256"].startswith("11c62128") and full["split"] == "dev"


REAL = Path(os.environ.get("SCIFACT_DATA_DIR", Path(__file__).resolve().parents[1] / ".tmp" / "external" / "scifact_data" / "data"))


@pytest.mark.skipif(not (REAL / "claims_dev.jsonl").is_file(), reason="the licensed SciFact archive has not been downloaded here (set SCIFACT_DATA_DIR)")
def test_on_the_real_scifact_dev_set_the_selector_is_scored_deterministically_and_beats_chance():
    r1, r2 = SF.evaluate(REAL, "dev"), SF.evaluate(REAL, "dev")
    assert r1 == r2                                                                            # reproducible
    assert r1["claims_in_split"] == 300 and r1["corpus_abstracts"] == 5183
    sent = r1["sentence_level_within_document_statistics"]
    assert sent["claims_with_evidence"] == 188
    for k, v in sent["at_k"].items():
        assert v["any_rationale_in_top_k"] >= v["chance_any_rationale_in_top_k"]               # a lexical selector does better than a random ranking, or this test says it does not
    assert r1["abstract_level"]["at_k"]["50"]["any_cited_abstract_in_top_k"] > r1["abstract_level"]["at_k"]["50"]["chance_any_cited_abstract_in_top_k"]
