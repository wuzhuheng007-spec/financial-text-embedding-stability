"""Explicit BM25 scoring and a separately named BM25-term-weight cosine.

Not a dense embedding, a symmetric BM25 kernel, or a return covariance estimate.
No network or model dependency. Fit statistics belong to one frozen corpus.
"""
from __future__ import annotations

from collections import Counter
import math
import re

TOKENIZER_ID = "ascii_alphanumeric_lower_v1"


def tokenize(text: str) -> list[str]:
    if not isinstance(text, str):
        raise ValueError("Text must be a string")
    return re.findall(r"[a-z0-9]+", text.lower())


class BM25:
    """Positive Robertson/Lucene-style IDF; unique query terms; exact lengths.

    Term contribution: log(1+(N-df+.5)/(df+.5)) * f*(k1+1) /
    (f+k1*(1-b+b*length/avg_length)). Includes the k1+1 multiplier.
    No stemming, stopword removal, query term-frequency or quantized norms.
    """
    def __init__(self, documents: list[list[str]], *, k1: float = 1.2, b: float = .75):
        if not math.isfinite(k1) or k1 <= 0 or not math.isfinite(b) or not 0 <= b <= 1:
            raise ValueError("Require finite k1 > 0 and 0 <= b <= 1")
        if not documents or any(not d for d in documents):
            raise ValueError("Empty corpus or token-empty document; no silent exclusion")
        if any(not isinstance(t, str) or not t for d in documents for t in d):
            raise ValueError("Tokens must be nonempty strings")
        self.k1, self.b = float(k1), float(b)
        self.counts = [Counter(d) for d in documents]
        self.lengths = [sum(c.values()) for c in self.counts]
        self.avg_length = sum(self.lengths) / len(self.lengths)
        df = Counter(t for c in self.counts for t in c)
        n = len(documents)
        self.idf = {t: math.log1p((n - f + .5) / (f + .5)) for t, f in df.items()}
        self.weights = [
            {t: self.idf[t] * f * (self.k1 + 1) /
             (f + self.k1 * (1 - self.b + self.b * length / self.avg_length))
             for t, f in c.items()}
            for c, length in zip(self.counts, self.lengths)
        ]

    def scores(self, query: list[str]) -> list[float]:
        if not query or any(not isinstance(t, str) or not t for t in query):
            raise ValueError("Query tokens must be nonempty strings")
        terms = sorted(set(query))
        return [math.fsum(row.get(t, 0.0) for t in terms) for row in self.weights]

    def termweight_cosine(self) -> list[list[float]]:
        norms = [math.sqrt(math.fsum(v*v for v in row.values())) for row in self.weights]
        if any(not math.isfinite(n) or n <= 0 for n in norms):
            raise ValueError("Zero/nonfinite lexical vector; no imputation")
        matrix = []
        for i, a in enumerate(self.weights):
            line = []
            for j, c in enumerate(self.weights):
                small, large = (a, c) if len(a) <= len(c) else (c, a)
                line.append(math.fsum(v * large.get(t, 0.) for t, v in small.items()) / (norms[i] * norms[j]))
            matrix.append(line)
        return matrix
