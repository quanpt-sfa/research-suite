"""Deterministic lexical retrieval: a tokenizer and Okapi BM25 over stored units (M5 WP5, stage R1).

No dependency, no model, no randomness: the same corpus and the same query give the same ranking. A retrieval score ranks what to read; it is never
a verdict about anything (I1). The tokenizer is versioned so a change of it is a change of method that the run records.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from typing import Iterable, Optional

TOKENIZER_VERSION = "v1"
STOPWORDS = frozenset("""a about above after again against all also am an and any are as at be because been before being below between both but by can could did do does
doing down during each few for from further had has have having he her here hers him his how i if in into is it its itself just may me more most my
no nor not now of off on once only or other our ours out over own same she should so some such than that the their theirs them then there these they
this those through to too under until up us very was we were what when where which while who whom why will with would you your based using use used
study paper results result""".split())
_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def stem(word: str) -> str:
    """A deliberately light stemmer: plural forms only, so that no two unrelated words are merged."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is", "as", "os")):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    t = unicodedata.normalize("NFKC", text or "").casefold()
    return [stem(w) for w in _WORD.findall(t) if len(w) >= 2 and not w.isdigit() and w not in STOPWORDS]


def phrase_tokens(phrases: Iterable[str]) -> list[str]:
    out: list[str] = []
    for p in phrases:
        out.extend(tokenize(p))
    return out


class Bm25:
    """Okapi BM25 over a fixed collection of units (unit id -> tokens)."""

    def __init__(self, docs: dict[str, list[str]], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf: dict[str, Counter] = {d: Counter(toks) for d, toks in docs.items()}
        self.len: dict[str, int] = {d: len(toks) for d, toks in docs.items()}
        self.n = len(docs)
        self.avgdl = (sum(self.len.values()) / self.n) if self.n else 0.0
        df: Counter = Counter()
        self.postings: dict[str, list[str]] = {}
        for d in sorted(self.tf):
            for term in self.tf[d]:
                df[term] += 1
                self.postings.setdefault(term, []).append(d)
        self.df = df

    def idf(self, term: str) -> float:
        n = self.df.get(term, 0)
        return math.log(1.0 + (self.n - n + 0.5) / (n + 0.5)) if n else 0.0

    def score(self, query: Iterable[str]) -> dict[str, float]:
        """Score of every unit that contains at least one query term (each distinct term counted once)."""
        scores: dict[str, float] = {}
        for term in sorted(set(query)):
            idf = self.idf(term)
            for d in self.postings.get(term, []):
                f = self.tf[d][term]
                norm = self.k1 * (1.0 - self.b + self.b * self.len[d] / self.avgdl) if self.avgdl else self.k1
                scores[d] = scores.get(d, 0.0) + idf * f * (self.k1 + 1.0) / (f + norm)
        return scores

    def matched(self, unit: str, query: Iterable[str]) -> dict[str, int]:
        c = self.tf.get(unit, Counter())
        return {t: c[t] for t in sorted(set(query)) if c.get(t)}


def best_per_source(scores: dict[str, float], unit_source: dict[str, str]) -> list[tuple[str, float, str]]:
    """(source id, best unit score, that unit's id), best first; ties broken by source id then unit id, so the order is reproducible."""
    best: dict[str, tuple[float, str]] = {}
    for unit, sc in scores.items():
        sid = unit_source[unit]
        cur: Optional[tuple[float, str]] = best.get(sid)
        if cur is None or sc > cur[0] or (sc == cur[0] and unit < cur[1]):
            best[sid] = (sc, unit)
    return sorted(((sid, sc, u) for sid, (sc, u) in best.items()), key=lambda t: (-round(t[1], 9), t[0], t[2]))
