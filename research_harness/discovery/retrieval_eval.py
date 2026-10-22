"""Evaluation of the retrieval against gold relevance labels (M5 WP5).

The labels are written by a person (or fixed in a test fixture) and say which full-text sources answer each query; nothing here is graded by a model.
For every query it reports R1 alone and R1 with the accepted expansions (R1+R2): recall@k, precision@k and the reciprocal rank of the first relevant source,
and where the relevant papers that are held but not readable (abstract-only or metadata-only) appear among the not-read candidates.

Adoption rule for any change of the retrieval method: recall@k on the fixed benchmark does not drop, and both numbers are reported. Numbers on a real corpus are
reported, never tuned on.
"""
from __future__ import annotations

from typing import Any, Optional

from research_harness.discovery import text_index as X
from research_harness.discovery.retrieval import Index, merge_stages
from research_harness.state.store import StateStore


def _metrics(ranked: list[str], relevant: set[str], k: int) -> dict[str, Any]:
    top = ranked[:k]
    found = [s for s in top if s in relevant]
    first = next((i for i, s in enumerate(ranked, 1) if s in relevant), None)
    return {"recall_at_k": round(len(found) / len(relevant), 4) if relevant else None, "precision_at_k": round(len(found) / k, 4),
            "reciprocal_rank": round(1.0 / first, 4) if first else 0.0, "first_relevant_rank": first, "found_in_top_k": found,
            "missed": sorted(relevant - set(ranked[:k]))}


def evaluate(store: StateStore, gold: dict[str, Any], config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    index = Index(store, config)
    k, top_n = int(gold.get("k", 10)), int(gold.get("top_n", 50))
    out_queries = []
    for q in gold["queries"]:
        base = sorted(set(X.tokenize(q["text"])) | set(X.phrase_tokens(q.get("terms", []))))
        expanded = sorted(set(base) | set(X.phrase_tokens(q.get("accepted_expansions", []))))
        relevant = set(q["relevant"])
        row: dict[str, Any] = {"id": q["id"], "text": q["text"], "relevant": len(relevant)}
        r1 = index.rank("FULL_TEXT", base, top_n)
        r2 = index.rank("FULL_TEXT", expanded, top_n)
        for name, ranked in (("R1", [h["source_id"] for h in r1]), ("R1+R2", [h["source_id"] for h in merge_stages(r1, r2)])):       # the same merge as `search`
            row[name] = _metrics(ranked, relevant, k)
        found_by_r2_only = [s for s in row["R1+R2"]["found_in_top_k"] if s not in row["R1"]["found_in_top_k"]]
        row["found_only_with_expansions"] = found_by_r2_only
        not_read = {}
        for sid in q.get("relevant_not_full_text", []):
            rank = None
            for tier in ("ABSTRACT_ONLY", "METADATA_ONLY"):
                ids = [h["source_id"] for h in index.rank(tier, expanded, top_n)]
                if sid in ids:
                    rank = {"tier": tier, "rank": ids.index(sid) + 1}
            not_read[sid] = rank
        row["relevant_not_readable"] = not_read
        out_queries.append(row)
    mean = {}
    for name in ("R1", "R1+R2"):
        vals = [r[name] for r in out_queries if r[name]["recall_at_k"] is not None]
        mean[name] = {m: round(sum(v[m] for v in vals) / len(vals), 4) if vals else None for m in ("recall_at_k", "precision_at_k", "reciprocal_rank")}
    return {"k": k, "top_n": top_n, "tokenizer": X.TOKENIZER_VERSION, "bm25": index.cfg["bm25"], "queries": out_queries, "mean": mean,
            "note": "labels are fixed by the researcher or the fixture; a model grades nothing here"}
