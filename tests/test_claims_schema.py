"""WP6.2: the claim vocabularies, the one new object type, the new reading protocols, and that nothing that existed before changed. Synthetic and structural: `SCHEMA_TESTED`."""
from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator

from research_harness import taxonomy
from research_harness.core.model import OBJECT_TYPES, SERVER_ONLY_TYPES
from research_harness.protocols.schema import PROPOSABLE_TYPES, content_schema
from research_harness.workflows import reading_protocols as rp

EXISTING_TYPES_BEFORE = ("ResearchQuestion", "Theory", "Construct", "Mechanism", "Proxy", "Dataset", "Source", "EvidencePassage", "Analysis", "Result", "Claim", "ManuscriptArtifact",
                         "Adjudication", "Approval", "DomainWorkflow", "MethodPlaybook", "JournalPolicy", "KnowledgeItem", "ContributionArgument", "AnalysisPlan", "ResearchTask",
                         "ResearchProblem", "RetrievalRun", "Landscape")
# digests of the five reading protocols as they were at e743849, before any WP6.2 change: a pin that named one must still resolve
DIGESTS_BEFORE = {"RP_CONTRIBUTION@1": "84cf1a556488d9dde7f3ea0e58a9b71871cb240d79b1b9907af08d4f0cc86dc5",
                  "RP_INTRO_ARGUMENT@2": "fb25933270cb2a895d51f5a6ed1d428f2483346e4718ee6a4f5ab8f0e668eef5",
                  "RP_LANDSCAPE@2": "cc12296edd734e67499ce91142bd964aeaf3c5daf035f9e89ac2846e8fce0e9c",
                  "RP_METHOD@4": "bc905a5cbd879c740dd1a7555638f6170fa9e645ea0a9b04066a710212a0d8cd",
                  "RP_THEORY@3": "5790fcac342f0b8216c11f74b25ecd5ef9dcf1e904a84a561e2bec979d297525"}


def test_attribution_and_role_are_two_separate_vocabularies_and_the_model_may_assign_only_three_attributions():
    attr = taxonomy.vocabulary("claim_attribution")
    assert attr == ("AUTHOR_OWN_CLAIM", "AUTHOR_REPORTS_PRIOR_WORK", "AUTHOR_CRITIQUES_PRIOR_WORK", "RESEARCHER_INTERPRETATION", "MODEL_PROPOSED_INTERPRETATION")
    assert set(taxonomy.vocabulary("claim_attribution_by_extraction")) == {"AUTHOR_OWN_CLAIM", "AUTHOR_REPORTS_PRIOR_WORK", "AUTHOR_CRITIQUES_PRIOR_WORK"}
    role = taxonomy.vocabulary("claim_role")
    assert set(role) == {"EMPIRICAL_FINDING", "INTERPRETIVE_FINDING", "THEORETICAL_PROPOSITION", "METHODOLOGICAL_CLAIM", "DEFINITION", "CLAIMED_CONTRIBUTION", "ASSUMPTION",
                         "BOUNDARY_CONDITION", "LIMITATION", "GAP_STATEMENT"}
    assert not set(attr) & set(role)                                     # an attribution is never a claim type, and the reverse


def test_the_eleven_relation_kinds_and_the_eight_version_changes_exist_and_reuse_the_older_claim_change_terms():
    assert set(taxonomy.vocabulary("claim_relation_kind")) == {"SUPPORTS", "CHALLENGES", "QUALIFIES", "DEFINES", "ASSUMES", "DEPENDS_ON", "EXTENDS", "RECONCEPTUALIZES",
                                                               "CONTRADICTS_WITHIN_SCOPE", "COMPLEMENTS", "SAME_IDEA_PROPOSED"}
    change = set(taxonomy.vocabulary("claim_version_change"))
    assert change == {"UNCHANGED", "QUALIFIED", "NARROWED", "WEAKENED", "STRENGTHENED", "REVERSED", "WITHDRAWN", "NEW_IN_LATER_VERSION"}
    assert {"UNCHANGED", "QUALIFIED", "NARROWED", "WEAKENED", "STRENGTHENED", "REVERSED"} <= set(taxonomy.vocabulary("claim_change"))     # the older vocabulary is reused, not copied under new names
    assert taxonomy.vocabulary("claim_change") == ("STRENGTHENED", "UNCHANGED", "NARROWED", "QUALIFIED", "WEAKENED", "REVERSED", "NOT_SUPPORTED")   # and is untouched


def test_the_new_object_type_is_appended_server_only_and_has_a_schema_while_every_older_type_keeps_its_place():
    assert OBJECT_TYPES[:len(EXISTING_TYPES_BEFORE)] == EXISTING_TYPES_BEFORE
    assert OBJECT_TYPES[-1] == "ClaimInventory" and "ClaimInventory" in SERVER_ONLY_TYPES and "ClaimInventory" not in PROPOSABLE_TYPES
    schema = content_schema("ClaimInventory")
    Draft202012Validator.check_schema(schema)
    ok = {"kind": "claim_inventory", "state": "BUILT", "sources": [], "elements": [], "relations": [], "alignments": []}
    assert not list(Draft202012Validator(schema).iter_errors(ok))
    assert list(Draft202012Validator(schema).iter_errors({"kind": "claim_inventory", "state": "BUILT"}))      # elements, relations, alignments and sources are required


def test_an_evidence_passage_written_before_this_work_still_validates_and_may_now_carry_attributes():
    schema = Draft202012Validator(content_schema("EvidencePassage"))
    old = {"label": "x", "locator": "SRC_A chars 0-20", "quote": "a verified quotation", "source_id": "SRC_A", "source_revision": 1, "char_start": 0, "char_end": 20, "quote_verified": True,
           "protocol": "RP_LANDSCAPE@2", "field": "finding", "value": "v", "generated_by": "model:x", "extraction_key": "k", "model": {}}
    assert not list(schema.iter_errors(old))
    assert not list(schema.iter_errors({**old, "attributes": {"attribution": "AUTHOR_OWN_CLAIM", "scope": {"population": {"value": "firms", "basis": "MODEL_INFERRED"}}}}))
    assert list(schema.iter_errors({**old, "attributes": "not an object"}))


def test_the_five_existing_reading_protocols_keep_their_digests_so_every_pin_and_cache_key_still_resolves():
    assert {p.pin: p.digest for p in rp.list_protocols() if p.pin in DIGESTS_BEFORE} == DIGESTS_BEFORE


def test_the_claims_protocol_has_no_record_cap_reads_every_section_but_the_references_and_is_scoped_to_the_model():
    p = rp.get_protocol("RP_CLAIMS")
    assert p.pin == "RP_CLAIMS@1" and p.model_scoped is True
    assert all("max_records" not in f for f in p.fields)                  # a claim count is never limited by configuration
    assert p.exclude_labels == ("references",) and p.all_sections is True
    names = {f["name"] for f in p.fields}
    assert names == {"empirical_finding", "interpretive_finding", "theoretical_proposition", "methodological_claim", "definition", "claimed_contribution", "assumption",
                     "boundary_condition", "limitation", "gap_statement"}
    allowed = set(taxonomy.vocabulary("claim_attribution_by_extraction"))
    assert all(set(f["types"]) == allowed for f in p.fields)              # `type` carries the attribution, and only attributions a model may assign
    ev = rp.get_protocol("RP_CLAIM_EVIDENCE")
    assert ev.pin == "RP_CLAIM_EVIDENCE@1" and ev.model_scoped is True and ev.all_sections is False


def test_a_protocol_that_uses_none_of_the_new_options_has_exactly_the_digest_it_had():
    base = rp.get_protocol("RP_THEORY")
    same = rp.ReadingProtocol(base.id, base.version, base.label, base.purpose, base.dimension, base.instructions, base.fields, base.extra_dimensions, base.include_unlabeled)
    assert same.digest == base.digest == DIGESTS_BEFORE["RP_THEORY@3"]
    scoped = rp.ReadingProtocol(base.id, base.version, base.label, base.purpose, base.dimension, base.instructions, base.fields, base.extra_dimensions, base.include_unlabeled,
                                model_scoped=True)
    assert scoped.digest != base.digest                                     # the option is part of what a pin means once it is used


@pytest.mark.parametrize("name", ["claim_attribute_basis", "claim_evidence_link_role", "claim_granularity", "cited_work_state", "claim_review_decision", "claim_scope_compatibility",
                                  "claim_relation_derivation"])
def test_the_supporting_vocabularies_are_defined(name):
    assert taxonomy.vocabulary(name)
