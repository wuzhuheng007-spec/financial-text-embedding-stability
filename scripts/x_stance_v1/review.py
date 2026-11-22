"""Human review of stance labels: the task file a reviewer fills in, and the scoring of what comes back.

Task
  * Drawn from EVERY pair of the sample that has an outcome under the active inference identity - including the
    ones the model excluded (IRRELEVANT) or could not judge. Sampling only OK labels would hide wrong exclusions.
  * Stratified by New York date, language and topic with an equal quota per stratum; each row carries its stratum
    size, the number drawn from it and the resulting inclusion probability, so results can be weighted back.
  * The reviewer file has the post, the target and two questions, and NO model output. The model's answers go to a
    separate sealed key file.
  * Frozen split: half development, half locked test, assigned by a hash of the post id and the seed. The locked
    half is scored once; a changed protocol needs a new task version, not a second look.

Scoring reports relevance (recall, wrong exclusions) separately from the three-class result (confusion matrix,
precision / recall / macro-F1 over pairs both sides judged), with coverage and abstention, each with its own
denominator and a Wilson interval, by language and by topic. A format-valid label is not an accurate label, and a
small sample is a check, not a validation.

Nothing here decides that quality is VALIDATED; that needs a frozen acceptance standard and real human labels.
Standard library only.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

from . import stance, store
from .config import LABELS

TASK_FORMAT = "x-stance-human-review-v1"
HUMAN_RELEVANCE = ("relevant", "not_relevant", "cannot_tell")


def candidates(db, cfg: dict, versions=None) -> list[dict]:
    """One row per (post, topic, target) of the sample that has an outcome under the active identity."""
    ident, out = stance.identity_hash(cfg), []
    for r in stance.work_items(db, cfg, versions):
        lab = db.execute("SELECT label, quality_status FROM x_stance_labels WHERE provider=? AND post_id=? AND topic_id=? AND target_id=? AND inference_identity=? ORDER BY label_written_at_utc DESC LIMIT 1",
                         (r["provider"], r["post_id"], r["topic_id"], r["target_id"], ident)).fetchone()
        if lab is None:
            continue
        out.append({"provider": r["provider"], "post_id": r["post_id"], "topic_id": r["topic_id"], "target_id": r["target_id"], "sample_day": r["sample_day"], "lang": r["lang"] or "und",
                    "kind": r["kind"], "queue": r["queue"], "text": r["author_text"], "source_url": r["source_url"], "model_label": lab["label"], "model_quality": lab["quality_status"]})
    return out


def build_task(rows: list[dict], *, n: int, seed: int, identity: str, created_at_utc: str | None = None) -> dict:
    """Equal quota per (sample_day, lang, topic) stratum, filled round-robin so no stratum is skipped; within a
    stratum the draw is random with the given seed. Returns {"reviewer": [...], "key": [...], "manifest": {...}}."""
    rng = random.Random(seed)
    strata = {}
    for r in sorted(rows, key=lambda r: (r["sample_day"], r["lang"], r["topic_id"], store.sha(r["post_id"] + r["topic_id"] + r["target_id"]))):
        strata.setdefault((r["sample_day"], r["lang"], r["topic_id"]), []).append(r)
    pools = {k: rng.sample(v, len(v)) for k, v in strata.items()}
    drawn, keys = {k: [] for k in strata}, sorted(strata)
    while sum(len(v) for v in drawn.values()) < n and any(pools[k] for k in keys):
        for k in keys:
            if sum(len(v) for v in drawn.values()) >= n:
                break
            if pools[k]:
                drawn[k].append(pools[k].pop())
    reviewer, key = [], []
    for k in keys:
        for r in drawn[k]:
            item = store.sha(f"{seed}|{r['provider']}|{r['post_id']}|{r['topic_id']}|{r['target_id']}")[:16]
            split = "development" if int(store.sha(f"split|{seed}|{r['post_id']}")[:8], 16) % 2 == 0 else "locked_test"
            base = {"item_id": item, "split": split, "stratum": {"sample_day": k[0], "lang": k[1], "topic_id": k[2]}, "stratum_size": len(strata[k]), "stratum_drawn": len(drawn[k]),
                    "inclusion_probability": len(drawn[k]) / len(strata[k])}
            reviewer.append({**base, "target_id": r["target_id"], "kind": r["kind"], "text": r["text"], "source_url": r["source_url"],
                             "human_relevance": None, "human_stance": None, "reviewer_note": None})
            key.append({**base, "provider": r["provider"], "post_id": r["post_id"], "topic_id": r["topic_id"], "target_id": r["target_id"], "model_label": r["model_label"], "model_quality": r["model_quality"]})
    by_split = {s: sum(1 for x in reviewer if x["split"] == s) for s in ("development", "locked_test")}
    man = {"format": TASK_FORMAT, "created_at_utc": created_at_utc or store.utcnow(), "seed": seed, "requested": n, "drawn": len(reviewer), "population_pairs": len(rows), "strata": len(strata),
           "by_split": by_split, "inference_identity": identity, "model_outcomes_in_population": _count(r["model_label"] or r["model_quality"] for r in rows),
           "model_outcomes_drawn": _count(k_["model_label"] or k_["model_quality"] for k_ in key),
           "instructions": ["First: is the post about the target and can the author's view be judged? (relevant / not_relevant / cannot_tell)",
                            "Only if relevant: the author's own stance on the target's future price (bullish / bearish / neutral)",
                            "The model's answer is not in this file. Do not look it up."],
           "locked_test_rule": "score the locked half once; a protocol change needs a new task version"}
    return {"reviewer": reviewer, "key": key, "manifest": man}


def _count(xs) -> dict:
    out = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items()))


def write_task(task: dict, folder: Path, name: str) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    files = {}
    for part in ("reviewer", "key"):
        p = folder / f"{name}.{part}.jsonl"
        p.write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in task[part]), encoding="utf-8", newline="\n")
        files[part] = {"file": p.name, "sha256": store.sha(p.read_text(encoding="utf-8"))}
    man = {**task["manifest"], "files": files, "privacy": "contains post text; private, not for the repository"}
    (folder / f"{name}.manifest.json").write_text(json.dumps(man, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return man


def wilson(k: float, n: float, z: float = 1.96) -> list[float] | None:
    if not n:
        return None
    p, d = k / n, 1 + z * z / n
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, (p + z * z / (2 * n)) / d - half), min(1.0, (p + z * z / (2 * n)) / d + half)]


def _rate(k, n) -> dict:
    return {"value": (k / n) if n else None, "numerator": k, "denominator": n, "wilson95": wilson(k, n)}


def score(reviewer: list[dict], key: list[dict], *, split: str) -> dict:
    """Unweighted counts with their denominators (the sample is small; weights are reported, not silently applied)."""
    k_by = {k["item_id"]: k for k in key}
    done = [r for r in reviewer if r["split"] == split and r.get("human_relevance") in HUMAN_RELEVANCE]
    if any(r["human_relevance"] == "relevant" and r.get("human_stance") not in LABELS for r in done):
        raise ValueError("a relevant item has no human stance")
    pairs = [(r, k_by[r["item_id"]]) for r in done]

    def part(ps):
        rel = [(r, k) for r, k in ps if r["human_relevance"] == "relevant"]
        notrel = [(r, k) for r, k in ps if r["human_relevance"] == "not_relevant"]
        kept = [(r, k) for r, k in rel if k["model_label"] in LABELS]
        m = {h: {c: 0 for c in list(LABELS) + ["EXCLUDED_OR_UNJUDGED"]} for h in LABELS}
        for r, k in rel:
            m[r["human_stance"]][k["model_label"] if k["model_label"] in LABELS else "EXCLUDED_OR_UNJUDGED"] += 1
        per = {}
        for lab in LABELS:
            tp = m[lab][lab]
            pred = sum(m[h][lab] for h in LABELS)
            actual = sum(m[lab][c] for c in LABELS)                      # among pairs the model also labelled
            p, rc = (tp / pred if pred else None), (tp / actual if actual else None)
            per[lab] = {"precision": p, "recall_among_model_labelled": rc, "f1": (2 * p * rc / (p + rc) if p and rc else (0.0 if (pred or actual) else None)), "support": actual}
        f1s = [v["f1"] for v in per.values() if v["f1"] is not None]
        return {"reviewed": len(ps), "human_relevant": len(rel), "human_not_relevant": len(notrel), "human_cannot_tell": len(ps) - len(rel) - len(notrel),
                "relevance_recall": _rate(len(kept), len(rel)), "wrong_exclusions": _rate(len(rel) - len(kept), len(rel)),
                "labelled_although_not_relevant": _rate(sum(1 for _, k in notrel if k["model_label"] in LABELS), len(notrel)),
                "three_class": {"denominator": "pairs a human called relevant AND the model labelled", "n": len(kept),
                                "accuracy": _rate(sum(1 for r, k in kept if r["human_stance"] == k["model_label"]), len(kept)),
                                "macro_f1": (sum(f1s) / len(f1s)) if f1s else None, "per_class": per,
                                "bull_bear_swaps": sum(1 for r, k in kept if {r["human_stance"], k["model_label"]} == {"bullish", "bearish"})},
                "confusion_human_rows_model_columns": m,
                "coverage": _rate(sum(1 for _, k in ps if k["model_label"] in LABELS), len(ps)), "model_abstention": _rate(sum(1 for _, k in ps if k["model_label"] not in LABELS), len(ps))}
    out = {"format": TASK_FORMAT, "split": split, "items_in_split": sum(1 for r in reviewer if r["split"] == split), **part(pairs),
           "by_language": {lg: part([(r, k) for r, k in pairs if r["stratum"]["lang"] == lg]) for lg in sorted({r["stratum"]["lang"] for r, _ in pairs})},
           "by_topic": {t: part([(r, k) for r, k in pairs if r["stratum"]["topic_id"] == t]) for t in sorted({r["stratum"]["topic_id"] for r, _ in pairs})},
           "inclusion_probability_range": [min((r["inclusion_probability"] for r, _ in pairs), default=None), max((r["inclusion_probability"] for r, _ in pairs), default=None)],
           "weighting": "counts are unweighted; strata were drawn with different probabilities (see each item), so these are not population estimates",
           "claim": "a check on this sample only; not a general accuracy figure and not a quality sign-off"}
    return out
