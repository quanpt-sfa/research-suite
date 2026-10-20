"""Supplementary reproductions: handoff propagation (I-11) and tokenizer-dependent usage_source (I-3)."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "D:/Works/research-suite")
from research_harness.state.store import StateStore  # noqa: E402
from research_harness.workflows.dispatcher import run_workflow, run_workflow_sequence  # noqa: E402
from research_harness.workflows.instrumentation import estimate_tokens  # noqa: E402

HERE = Path(__file__).resolve().parent / "projects2"
if HERE.exists():
    shutil.rmtree(HERE)
HERE.mkdir(parents=True)

mode = sys.argv[1] if len(sys.argv) > 1 else "handoff"
store, tok = StateStore.create(HERE / "h", "PRJ_H")
with store.write_tx(token=tok) as tx:
    tx.add_revision(object_id="RQ_TEST", object_type="ResearchQuestion",
                    content={"text": "Does board independence reduce restatements?"}, origin={}, edges=[])
    tx.add_revision(object_id="RES_1", object_type="Result",
                    content={"summary": "Board independence has a negative and significant coefficient on restatements (beta=-0.12, p<0.01), as H1 predicts."},
                    origin={}, edges=[("depends_on", "RQ_TEST", 1, {})])
    tx.add_revision(object_id="CLAIM_1", object_type="Claim",
                    content={"text": "H1: board independence reduces restatements.", "claim_tier": "associational"},
                    origin={}, edges=[("depends_on", "RQ_TEST", 1, {}), ("depends_on", "RES_1", 1, {})])

if mode == "tokenizer":
    n, is_est = estimate_tokens("hello world")
    o = run_workflow("methods", store, research_question_id="RQ_TEST", reuse_cached=False)
    print(json.dumps({"tiktoken_exact": not is_est, "llm_calls": o.usage.llm_calls, "usage_source": o.usage.usage_source,
                      "model": o.usage.model}))
    sys.exit(0)

seq = run_workflow_sequence(store, ["results", "discussion", "contribution"], research_question_id="RQ_TEST",
                            focus_claims=["CLAIM_1"], reuse_cached=False)
res, disc, con = seq
print("statuses:", [o.status for o in seq])
print("discussion.reopens:", disc.result["reopens_contribution_workflow"])
print("discussion.contradictions:", [c["reconciliation_status"] for c in disc.result["contradictions"]])
print("contribution.upstream_consumed:", con.result["upstream_artifacts_consumed"])
print("contribution.alternatives:", con.result["alternative_contribution_formulations"])
print("contribution.candidate:", con.result["candidate_contributions"][0]["proposed_contribution"])
print("contribution.confidence:", con.result["confidence_explanation"])
with store.read() as c:
    print("claim CLAIM_1 head_revision after chain:", c.execute("SELECT head_revision FROM object_heads WHERE object_id='CLAIM_1'").fetchone()[0])
