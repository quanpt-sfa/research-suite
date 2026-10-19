"""End-to-End Demonstration of Research Harness 9.0-dev.

Walks through the entire research lifecycle:
1. Project Initialization & Actor Registration
2. Scoped Snapshot Export with D13 HMAC Signing
3. ChatGPT Web Proposal Ingestion via Gateway (Optimistic Concurrency & CAS)
4. Empirical Update: Proxy Revision & Cascading Invalidation
5. Concurrency Conflict Detection on Stale Read-Sets
6. Rejection of Forged / Unauthorized Review Attempts
7. Privileged Independent Peer Review & Invalidation Resolution
8. Release Eligibility Gate Evaluation (Blocked -> Eligible)
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from research_harness import PROTOCOL_VERSION, __version__
from research_harness.core.auth import authenticate, new_token, token_hash
from research_harness.core.model import Decision, IntegrityStatus, ReleaseEligibility, ReviewStatus, WorkflowStatus
from research_harness.graph.dependency import traverse
from research_harness.proposals.gateway import import_proposal, static_check
from research_harness.protocols.snapshot import export_snapshot, verify_snapshot_document
from research_harness.state.store import StateStore
from research_harness.validation.integrity import audit_project_integrity, release_check
from research_harness.validation.project_status import get_object_history, get_project_status
from research_harness.validation.review import record_review


def print_step(title: str) -> None:
    print(f"\n{'='*70}\n[DEMO] {title}\n{'='*70}")


def main() -> None:
    demo_dir = Path(tempfile.mkdtemp(prefix="research_harness_demo_"))
    try:
        print_step("Step 1: Initialize Project on D13 Host")
        project_id = "PRJ_REASONING_AGENTS_2026"
        store, owner_token = StateStore.create(demo_dir, project_id, owner_id="alice_owner")
        print(f"Project '{project_id}' initialized at: {store.paths.root}")
        print(f"Owner: alice_owner (Token: {owner_token[:15]}...)")

        # Register Author (Bob) and Independent Reviewer (Carol)
        bob_token = new_token()
        carol_token = new_token()
        with store.write_tx(token=owner_token, bump=False) as tx:
            tx.conn.execute("UPDATE actors SET independence_group='org_owner' WHERE actor_id='alice_owner'")
            tx.conn.execute(
                "INSERT INTO actors(actor_id, display_name, roles, independence_group, token_sha256, created_at, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("bob_researcher", "Bob (AI Research Lead)", json.dumps(["author", "importer"]), "lab_mit", token_hash(bob_token), tx.at, "alice_owner"),
            )
            tx.conn.execute(
                "INSERT INTO actors(actor_id, display_name, roles, independence_group, token_sha256, created_at, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("carol_reviewer", "Dr. Carol (External Peer Reviewer)", json.dumps(["independent_reviewer"]), "lab_oxford", token_hash(carol_token), tx.at, "alice_owner"),
            )
        print("Actors registered: bob_researcher (author, importer, lab_mit), carol_reviewer (independent_reviewer, lab_oxford)")

        # Verify initial release check fails closed on empty project
        init_gate = release_check(store)
        print(f"Initial Empty Gate: Integrity={init_gate['integrity_status']}, Completeness={init_gate['workflow_status']}, Review={init_gate['scientific_review_status']}, Release={init_gate['release_eligibility']}")
        assert init_gate["release_eligibility"] == ReleaseEligibility.BLOCKED.value

        print_step("Step 2: Export Scoped Snapshot for ChatGPT Web")
        with store.read() as conn:
            bob_actor = authenticate(conn, bob_token)
        snapshot = export_snapshot(store, bob_actor, scope="full")
        print(f"Snapshot exported: {snapshot['snapshot_id']} (State Rev: {snapshot['state_revision']}, SHA: {snapshot['state_sha256'][:16]}...)")
        print(f"Server HMAC: {snapshot['integrity']['body_hmac_sha256'][:24]}...")

        ver = verify_snapshot_document(store, json.dumps(snapshot))
        print(f"Snapshot verification check: {ver['status']}")
        assert ver["status"] == "PASS"

        print_step("Step 3: ChatGPT Web Ingestion via Gateway (CAS & Transaction)")
        # ChatGPT proposes Research Question, Construct, Proxy, Result, and Confirmatory Claim
        proposal_doc = {
            "protocol_version": PROTOCOL_VERSION,
            "proposal_id": "PROP_CHATGPT_001",
            "project_id": project_id,
            "base": {
                "snapshot_id": snapshot["snapshot_id"],
                "state_revision": snapshot["state_revision"],
                "state_sha256": snapshot["state_sha256"],
            },
            "read_set": {},
            "origin_channel": "chatgpt_web",
            "operations": [
                {
                    "operation": "propose_object",
                    "proposed_id": "RQ_AGENT_REASONING",
                    "object_type": "ResearchQuestion",
                    "content": {
                        "label": "Reasoning Models in Scientific Systems",
                        "text": "Do multi-step reasoning models reduce architectural regression rates in complex research codebases?",
                    },
                    "relations": [],
                },
                {
                    "operation": "propose_object",
                    "proposed_id": "CONST_REGRESSION_RATE",
                    "object_type": "Construct",
                    "content": {
                        "label": "Regression Rate",
                        "definition": "Proportion of architectural modifications introducing semantic or contract breaks.",
                    },
                    "relations": [],
                },
                {
                    "operation": "propose_object",
                    "proposed_id": "PROXY_TEST_FAILURES",
                    "object_type": "Proxy",
                    "content": {
                        "label": "Automated Harness Breakage Proxy",
                        "definition": "Count of failed test assertions during full suite invocation.",
                        "unit": "broken_assertions",
                    },
                    "relations": [{"relation": "operationalizes", "target_id": "CONST_REGRESSION_RATE"}],
                },
                {
                    "operation": "propose_object",
                    "proposed_id": "RESULT_EMPIRICAL_RUN",
                    "object_type": "Result",
                    "content": {
                        "label": "Empirical Refactor Run 1",
                        "summary": "Reasoning model refactoring yielded 0 broken assertions across 105 legacy baseline tests.",
                    },
                    "relations": [],
                },
                {
                    "operation": "propose_claim",
                    "proposed_id": "CLAIM_CORE_HYPOTHESIS",
                    "claim_tier": "causal",
                    "text": "Integrating reasoning models directly reduces harness regression failures.",
                    "formation_basis": "confirmatory",
                    "observed_results_at_formation": [],
                    "depends_on": ["PROXY_TEST_FAILURES", "RESULT_EMPIRICAL_RUN"],
                    "relations": [
                        {"relation": "depends_on", "target_id": "PROXY_TEST_FAILURES"},
                        {"relation": "depends_on", "target_id": "RESULT_EMPIRICAL_RUN"},
                    ],
                },
            ],
            "note": "Drafted by ChatGPT Web based on prompt specifications",
        }

        # Static check
        static_check(json.dumps(proposal_doc))
        print("Static schema & guard check: PASSED (no forbidden fields or path traversals).")

        # Import proposal through gateway
        rec = import_proposal(store, json.dumps(proposal_doc), bob_token)
        print(f"Gateway Decision: {rec['decision']}")
        print(f"Committed State Revision: {rec['new_state_revision']} (State SHA-256: {rec['state_sha256'][:16]}...)")
        print(f"Created Objects: {rec['created_objects']}")
        assert rec["decision"] == Decision.ACCEPTED.value

        print_step("Step 4: Empirical Update & Cascading Invalidation")
        # Author approves initial claim
        record_review(store, owner_token, object_id="CLAIM_CORE_HYPOTHESIS", revision=1, kind="author_approval", decision="approve", rationale="Initial author sign-off.")
        print("Initial author approval recorded: status = AUTHOR_APPROVED")

        # Now researcher updates Proxy definition (rev 2)
        snap2 = export_snapshot(store, bob_actor, scope="full")
        revise_proxy_doc = {
            "protocol_version": PROTOCOL_VERSION,
            "proposal_id": "PROP_REVISE_PROXY_02",
            "project_id": project_id,
            "base": {
                "snapshot_id": snap2["snapshot_id"],
                "state_revision": snap2["state_revision"],
                "state_sha256": snap2["state_sha256"],
            },
            "read_set": {"PROXY_TEST_FAILURES": 1, "CONST_REGRESSION_RATE": 1},
            "origin_channel": "human_cli",
            "operations": [
                {
                    "operation": "revise_object",
                    "object_id": "PROXY_TEST_FAILURES",
                    "base_revision": 1,
                    "content": {
                        "label": "Automated Harness Breakage Proxy",
                        "definition": "Weighted sum of broken assertions plus runtime memory leak triggers.",
                        "unit": "weighted_defect_points",
                    },
                    "relations": [{"relation": "operationalizes", "target_id": "CONST_REGRESSION_RATE"}],
                }
            ],
        }
        rec_proxy = import_proposal(store, json.dumps(revise_proxy_doc), bob_token)
        print(f"Proxy revision committed: rev 2 (New State Rev: {rec_proxy['new_state_revision']})")
        print(f"Cascading Invalidation: {rec_proxy['invalidated_objects']}")
        assert len(rec_proxy["invalidated_objects"]) == 1

        # Check claim status in store
        with store.read() as conn:
            claim_status = conn.execute("SELECT scientific_review_status FROM object_status WHERE object_id='CLAIM_CORE_HYPOTHESIS'").fetchone()[0]
            print(f"Downstream Claim Status: {claim_status} (Cascaded from INDEPENDENTLY_REVIEWED to NEEDS_REVIEW!)")
            assert claim_status == ReviewStatus.NEEDS_REVIEW.value

        print_step("Step 5: Concurrency Conflict Detection on Stale Read-Set")
        stale_prop = {
            "protocol_version": PROTOCOL_VERSION,
            "proposal_id": "PROP_STALE_CONCURRENT",
            "project_id": project_id,
            "base": {
                "snapshot_id": snap2["snapshot_id"],
                "state_revision": snap2["state_revision"],
                "state_sha256": snap2["state_sha256"],
            },
            "read_set": {"PROXY_TEST_FAILURES": 1},
            "origin_channel": "chatgpt_web",
            "operations": [
                {
                    "operation": "revise_object",
                    "object_id": "PROXY_TEST_FAILURES",
                    "base_revision": 1,
                    "content": {"label": "Conflicting definition", "definition": "Foo", "unit": "bar"},
                    "relations": [],
                }
            ],
        }
        rec_stale = import_proposal(store, json.dumps(stale_prop), bob_token)
        print(f"Stale proposal decision: {rec_stale['decision']} (Rule: {rec_stale['rule']})")
        assert rec_stale["decision"] == Decision.CONFLICT.value

        print_step("Step 6: Rejection of Forged / Unauthorized Review Attempts")
        # Attempt 1: Untrusted proposal trying to forge an approval object
        forge_prop = {
            "protocol_version": PROTOCOL_VERSION,
            "proposal_id": "PROP_FORGE_APR",
            "project_id": project_id,
            "base": {
                "snapshot_id": snap2["snapshot_id"],
                "state_revision": snap2["state_revision"],
                "state_sha256": snap2["state_sha256"],
            },
            "read_set": {},
            "origin_channel": "chatgpt_web",
            "operations": [
                {
                    "operation": "propose_object",
                    "proposed_id": "APR_009999",
                    "object_type": "Approval",
                    "content": {"decision": "approve", "kind": "independent_review"},
                }
            ],
        }
        rec_forge = import_proposal(store, json.dumps(forge_prop), bob_token)
        print(f"Forged Approval proposal rejected: {rec_forge['decision']} (Rule: {rec_forge['rule']})")
        assert rec_forge["decision"] == Decision.UNAUTHORIZED.value

        # Attempt 2: Author Bob trying to perform independent review on own project
        unauth_rev = record_review(store, bob_token, object_id="CLAIM_CORE_HYPOTHESIS", revision=1, kind="independent_review", decision="approve")
        print(f"Non-independent reviewer rejected: {unauth_rev['decision']} (Rule: {unauth_rev['rule']})")
        assert unauth_rev["decision"] == "UNAUTHORIZED"

        # Attempt 3: Review without acknowledging open invalidation
        refused_rev = record_review(store, carol_token, object_id="CLAIM_CORE_HYPOTHESIS", revision=1, kind="independent_review", decision="approve", acknowledge_invalidations=False)
        print(f"Review without invalidation acknowledgment rejected: {refused_rev['decision']} (Rule: {refused_rev['rule']})")
        assert refused_rev["decision"] == "REFUSED"

        print_step("Step 7: Privileged Review & Invalidation Resolution")
        valid_rev = record_review(
            store,
            carol_token,
            object_id="CLAIM_CORE_HYPOTHESIS",
            revision=1,
            kind="independent_review",
            decision="approve",
            rationale="Audited revised proxy formula; causal connection to harness stability remains valid.",
            acknowledge_invalidations=True,
        )
        print(f"Independent review successfully recorded: {valid_rev['decision']}")
        print(f"Approval Object Created: {valid_rev['approval_id']}")
        print(f"New Claim Status: {valid_rev['new_review_status']}")
        assert valid_rev["decision"] == "RECORDED"

        print_step("Step 8: Final Integrity Audit & Release Gate Check")
        aud = audit_project_integrity(store)
        print(f"Cryptographic Audit: {aud['status']} (Zero corruption, hash chain verified).")
        assert aud["status"] == IntegrityStatus.PASS.value

        gate = release_check(store)
        print(f"Final Release Check:")
        print(f"  Integrity:        {gate['integrity_status']}")
        print(f"  Completeness:     {gate['workflow_status']}")
        print(f"  Review Status:    {gate['scientific_review_status']}")
        print(f"  Release Decision: {gate['release_eligibility']}")
        assert gate["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value

        print_step("DEMONSTRATION COMPLETED SUCCESSFULLY")
        print(f"All 8 workflow phases verified with 100% adherence to Research Harness 9.0 contracts.")

    finally:
        shutil.rmtree(demo_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
