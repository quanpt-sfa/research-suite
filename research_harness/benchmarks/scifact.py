"""SciFact (Wadden et al. 2020) as a benchmark of one narrow component: how well a lexical selector finds the sentences that bear on a claim (M5 WP6.2).

What SciFact is: claims written from citation sentences, each linked to the abstracts that SUPPORT or CONTRADICT it and to the rationale sentences of those abstracts. Biomedical, abstracts only,
English. What it is not: a benchmark of claim extraction, of attribution, of conceptual papers or of critical reviews; its SUPPORT/CONTRADICT is a verdict on a claim, not a theoretical incompatibility.

What is measured here, with no model: the BM25 selector the claim follow-up uses (`claims._candidate_spans`, `text_index.Bm25`) asked, for each dev claim, to
(1) rank the sentences of the abstract that holds the evidence, scored by whether a rationale sentence is in the top k (`sentence level`), and
(2) rank all abstracts of the corpus, scored by whether the cited abstract is in the top k (`abstract level`).
A random ranking is reported beside each figure, so a number is read against what chance gives.

Licence: the claims and evidence annotations are CC BY 4.0, the abstracts ODC-By 1.0 (S2ORC), the code Apache-2.0 (`LICENSE.md` of allenai/scifact). The test labels are not public, so only train and
dev can be scored. The data are not part of this repository: the archive is downloaded by the person who runs the benchmark, and its sha256 is recorded in the result.
"""
from __future__ import annotations

import hashlib
import json
from math import comb
from pathlib import Path
from typing import Any, Optional

from research_harness.discovery import text_index as X
from research_harness.knowledge.common import KnowledgeError

ARCHIVE = {"url": "https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz", "bytes": 3115079,
           "sha256": "11c621288d41ac144d29b13b0f8503b3820b7d6e8b1f6ff24dff335c196d76be", "repository": "https://github.com/allenai/scifact"}
LICENCE = {"claims_and_annotations": "CC BY 4.0", "abstracts": "ODC-By 1.0 (S2ORC)", "code": "Apache-2.0", "source": "allenai/scifact LICENSE.md",
           "redistribution": "the data are not committed to this repository"}
SPLITS = ("train", "dev")
KS_SENTENCE = (1, 2, 3, 5)
KS_ABSTRACT = (1, 5, 10, 50)


def load(data_dir: Path | str, split: str = "dev") -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    """Claims of a labelled split and the corpus. The test split has no labels and cannot be scored."""
    if split not in SPLITS:
        raise KnowledgeError("SPLIT_NOT_LABELLED", f"{split!r} has no public labels; the labelled splits are {SPLITS}")
    d = Path(data_dir)
    for name in (f"claims_{split}.jsonl", "corpus.jsonl"):
        if not (d / name).is_file():
            raise KnowledgeError("DATA_UNAVAILABLE", f"{d / name} not found: download the SciFact archive ({ARCHIVE['url']}) and extract it")
    claims = [json.loads(line) for line in (d / f"claims_{split}.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    corpus = {}
    for line in (d / "corpus.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            doc = json.loads(line)
            corpus[int(doc["doc_id"])] = doc
    for c in claims:
        for doc_id, rationales in (c.get("evidence") or {}).items():
            if int(doc_id) not in corpus:
                raise KnowledgeError("DATA_INCONSISTENT", f"claim {c['id']} cites document {doc_id}, which is not in the corpus")
            for r in rationales:
                if r["label"] not in ("SUPPORT", "CONTRADICT") or any(s >= len(corpus[int(doc_id)]["abstract"]) for s in r["sentences"]):
                    raise KnowledgeError("DATA_INCONSISTENT", f"claim {c['id']} has a rationale outside the abstract or with an unknown label")
    return claims, corpus


def sha256_of(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _chance_any(n: int, r: int, k: int) -> float:
    """Probability that a random ranking of n items puts at least one of r relevant ones in the top k."""
    k = min(k, n)
    return 1.0 - (comb(n - r, k) / comb(n, k)) if n and r and n - r >= k else (1.0 if n and r else 0.0)


def sentence_level(claims: list[dict[str, Any]], corpus: dict[int, dict[str, Any]], *, ks: tuple[int, ...] = KS_SENTENCE, idf: str = "within_document") -> dict[str, Any]:
    """For each (claim, evidence abstract): rank the abstract's sentences by BM25 against the claim and score the top k against the rationale sentences.
    `idf`: `within_document` (statistics of this one abstract: what the follow-up has, since it reads one paper) or `corpus` (statistics of all abstracts)."""
    global_bm: Optional[X.Bm25] = None
    unit_of: dict[tuple[int, int], str] = {}
    if idf == "corpus":
        docs = {}
        for did, doc in corpus.items():
            for i, s in enumerate(doc["abstract"]):
                u = f"{did}:{i}"
                unit_of[(did, i)] = u
                docs[u] = X.tokenize(s)
        global_bm = X.Bm25(docs)
    pairs = n_rat = 0
    any_hit = {k: 0 for k in ks}
    recall = {k: 0.0 for k in ks}
    chance = {k: 0.0 for k in ks}
    n_sent = 0
    for c in claims:
        q = X.tokenize(c["claim"])
        all_scores = global_bm.score(q) if global_bm is not None and c.get("evidence") else {}
        for doc_id, rationales in (c.get("evidence") or {}).items():
            did = int(doc_id)
            sents = corpus[did]["abstract"]
            gold = sorted({s for r in rationales for s in r["sentences"]})
            if not gold:
                continue
            if global_bm is not None:
                sc = {i: all_scores.get(unit_of[(did, i)], 0.0) for i in range(len(sents))}
            else:
                bm = X.Bm25({str(i): X.tokenize(s) for i, s in enumerate(sents)})
                raw = bm.score(q)
                sc = {i: raw.get(str(i), 0.0) for i in range(len(sents))}
            ranking = sorted(range(len(sents)), key=lambda i: (-round(sc[i], 9), i))                 # ties by position: reproducible, and not in the selector's favour
            pairs += 1
            n_rat += len(gold)
            n_sent += len(sents)
            for k in ks:
                top = set(ranking[:k])
                hit = len(top & set(gold))
                any_hit[k] += 1 if hit else 0
                recall[k] += hit / len(gold)
                chance[k] += _chance_any(len(sents), len(gold), k)
    return {"idf": idf, "claims_with_evidence": sum(1 for c in claims if c.get("evidence")), "claim_abstract_pairs": pairs, "rationale_sentences": n_rat,
            "mean_sentences_per_abstract": round(n_sent / pairs, 2) if pairs else None,
            "at_k": {str(k): {"any_rationale_in_top_k": round(any_hit[k] / pairs, 4) if pairs else None, "rationale_sentence_recall": round(recall[k] / pairs, 4) if pairs else None,
                              "chance_any_rationale_in_top_k": round(chance[k] / pairs, 4) if pairs else None} for k in ks}}


def abstract_level(claims: list[dict[str, Any]], corpus: dict[int, dict[str, Any]], *, ks: tuple[int, ...] = KS_ABSTRACT) -> dict[str, Any]:
    """For each claim with evidence: rank every abstract of the corpus (title and abstract) by BM25 against the claim; score the cited abstracts that hold evidence."""
    ids = sorted(corpus)
    bm = X.Bm25({str(d): X.tokenize(corpus[d]["title"] + " " + " ".join(corpus[d]["abstract"])) for d in ids})
    n_claims = 0
    any_hit = {k: 0 for k in ks}
    recall = {k: 0.0 for k in ks}
    chance = {k: 0.0 for k in ks}
    for c in claims:
        gold = {int(d) for d, rs in (c.get("evidence") or {}).items() if rs}
        if not gold:
            continue
        sc = bm.score(X.tokenize(c["claim"]))
        ranking = sorted(ids, key=lambda d: (-round(sc.get(str(d), 0.0), 9), d))
        n_claims += 1
        for k in ks:
            hit = len(set(ranking[:k]) & gold)
            any_hit[k] += 1 if hit else 0
            recall[k] += hit / len(gold)
            chance[k] += _chance_any(len(ids), len(gold), k)
    return {"claims_with_evidence": n_claims, "abstracts_ranked": len(ids),
            "at_k": {str(k): {"any_cited_abstract_in_top_k": round(any_hit[k] / n_claims, 4) if n_claims else None, "cited_abstract_recall": round(recall[k] / n_claims, 4) if n_claims else None,
                              "chance_any_cited_abstract_in_top_k": round(chance[k] / n_claims, 4) if n_claims else None} for k in ks}}


def evaluate(data_dir: Path | str, split: str = "dev", *, archive: Optional[Path | str] = None) -> dict[str, Any]:
    """The whole SciFact check of the lexical selector on a labelled split, with what a reader needs to judge it."""
    claims, corpus = load(data_dir, split)
    out = {"benchmark": "SciFact", "split": split, "task_ids": [4, 5], "what_is_measured": "a BM25 selector's ranking of evidence sentences and abstracts for a claim (no model)",
           "what_is_not": "claim extraction, attribution, conceptual or critical-review papers, SUPPORT/CONTRADICT verification",
           "domain": "biomedical abstracts", "licence": LICENCE, "archive": {**ARCHIVE, **({"sha256_of_file_used": sha256_of(archive)} if archive else {})},
           "claims_in_split": len(claims), "corpus_abstracts": len(corpus),
           "sentence_level_within_document_statistics": sentence_level(claims, corpus, idf="within_document"),
           "sentence_level_corpus_statistics": sentence_level(claims, corpus, idf="corpus"), "abstract_level": abstract_level(claims, corpus),
           "reading": "figures are fractions of claims (or claim-abstract pairs); the chance column is what a random ranking gives; ties are broken by position so the result is reproducible and not in the selector's favour"}
    return out
