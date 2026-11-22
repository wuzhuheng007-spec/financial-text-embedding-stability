"""Main views in the sample: at most five clusters of the window's vote posts (one post per account, so an
account supports exactly one cluster). Hashed TF-IDF and spherical k-means on sparse rows, fixed seed; no pairwise
similarity matrix is built. Titles are an exact excerpt of the most central post, not generated text, and every
cluster carries the post ids and links it was built from. A cluster below the minimum support is not shown, and
nothing is invented to reach five. Clustering a claim does not check whether the claim is true.
"""
from __future__ import annotations

import hashlib
import re

import numpy as np
from scipy import sparse

from .config import LABELS

DIM = 1 << 15
_WORD = re.compile(r"[$#]?[a-z0-9][a-z0-9'&\-]*")
_CJK = re.compile(r"[㐀-鿿]+")
_URL = re.compile(r"https?://\S+|@\w+")
_STOP = frozenset("a an the and or but of to in on for with at by from is are was were be been it this that these those i you he she we they my your "
                  "as so if not no just very will would can could should have has had do does did rt amp".split())


def tokens(text: str) -> list[str]:
    t = _URL.sub(" ", text.lower())
    out = [w for w in _WORD.findall(t) if w not in _STOP and len(w) > 1]
    out += [a + " " + b for a, b in zip(out, out[1:])]
    for run in _CJK.findall(t):
        out += [run[i:i + 2] for i in range(len(run) - 1)] or [run]
    return out


def _h(tok: str) -> int:
    return int.from_bytes(hashlib.blake2b(tok.encode("utf-8"), digest_size=4).digest(), "little") % DIM


def vectorize(texts: list[str]) -> sparse.csr_matrix:
    rows, cols, vals = [], [], []
    for i, t in enumerate(texts):
        counts = {}
        for tok in tokens(t):
            j = _h(tok); counts[j] = counts.get(j, 0) + 1
        for j, c in counts.items():
            rows.append(i); cols.append(j); vals.append(1.0 + np.log(c))
    m = sparse.csr_matrix((vals, (rows, cols)), shape=(len(texts), DIM), dtype=np.float64)
    df = np.asarray((m > 0).sum(axis=0)).ravel()
    m = m @ sparse.diags(np.log((1 + len(texts)) / (1 + df)) + 1.0)
    norm = np.sqrt(np.asarray(m.multiply(m).sum(axis=1)).ravel()); norm[norm == 0] = 1.0
    return (sparse.diags(1.0 / norm) @ m).tocsr()


def _kmeans(m: sparse.csr_matrix, k: int, seed: int, iters: int = 25) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    centers = m[np.sort(rng.choice(m.shape[0], size=k, replace=False))].toarray()
    assign = np.full(m.shape[0], -1)
    for _ in range(iters):
        sim = m @ centers.T
        new = np.asarray(sim.argmax(axis=1)).ravel()
        if (new == assign).all():
            break
        assign = new
        for c in range(k):
            idx = np.flatnonzero(assign == c)
            if len(idx):
                v = np.asarray(m[idx].sum(axis=0)).ravel(); n = np.linalg.norm(v)
                centers[c] = v / n if n else v
    return assign, np.asarray(m @ centers.T)


def build(votes: list, *, cfg: dict, window_label: str, window_kind: str, data_quality: str) -> list[dict]:
    """votes: one row per account (aggregate.votes). Returns 0..5 narratives ranked by supporting accounts."""
    n = cfg["narratives"]
    votes = [v for v in votes if (v["reason_short"] or v["author_text"] or "").strip()]
    if len(votes) < n["proposed_min_supporting_authors"]:
        return []
    texts = [((v["reason_short"] or "") + " " + v["author_text"]).strip() for v in votes]
    m = vectorize(texts)
    k = max(1, min(n["proposed_candidate_clusters"], len(votes) // max(1, n["proposed_min_supporting_authors"])))
    assign, sim = _kmeans(m, k, n["proposed_seed"])
    out = []
    for c in range(k):
        idx = np.flatnonzero(assign == c)
        if len(idx) < n["proposed_min_supporting_authors"]:
            continue
        order = idx[np.lexsort((np.array([votes[i]["post_id"] for i in idx]), -sim[idx, c]))]   # most central first, post id breaks ties
        rep = [votes[i] for i in order[:3]]
        comp = {lab: int(sum(1 for i in idx if votes[i]["label"] == lab)) for lab in LABELS}
        title = " ".join(_URL.sub(" ", rep[0]["author_text"]).split())
        out.append({"title": title[:140], "title_method": "EXACT_EXCERPT_OF_MOST_CENTRAL_POST", "supporting_accounts": int(len(idx)),
                    "share_of_sample_accounts": len(idx) / len(votes), "stance_composition": comp,
                    "evidence": [{"post_id": r["post_id"], "url": r["source_url"], "stance": r["label"]} for r in rep],
                    "window_label": window_label, "window_kind": window_kind, "data_quality": data_quality,
                    "fact_check_status": "NOT_CHECKED", "scope": "views in this retrieved sample, not a ranking of all X",
                    "cluster_version": n["cluster_version"], "seed": n["proposed_seed"]})
    out.sort(key=lambda d: (-d["supporting_accounts"], d["evidence"][0]["post_id"]))
    out = out[: min(5, n["max_clusters_to_display"])]
    for i, d in enumerate(out, 1):
        d["rank"] = i
    return out
