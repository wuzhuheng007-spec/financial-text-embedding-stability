"""Local, source-bound LlamaIndex retrieval; separate from the M2-M7 study pipeline."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys
from typing import Any

import yaml

from .config import Config, load_config
from .lexical import BM25, TOKENIZER_ID, tokenize
from .model import Chunk
from .run import sha256


LLAMA_CORE_VERSION = "0.14.25"
RAG_SCHEMA_VERSION = 1


class RagError(RuntimeError):
    """A required input, model, index, or retrieval condition is invalid."""


@dataclass(frozen=True)
class RagSettings:
    source: Path
    source_sha256: str
    root: Path
    model_config: Config
    body_tokens: int
    overlap_tokens: int
    bm25_k1: float
    bm25_b: float
    rrf_k: int
    bm25_weight: float
    dense_weight: float
    branch_top_k: int
    top_k: int


def _llama_types():
    try:
        from llama_index.core.schema import (
            Document, MetadataMode, NodeRelationship, NodeWithScore,
            QueryBundle, RelatedNodeInfo, TextNode,
        )
        from llama_index.core.base.base_retriever import BaseRetriever
    except ImportError as exc:
        raise RagError("Install the optional pinned LlamaIndex core dependency (project [rag] extra)") from exc
    version = importlib.metadata.version("llama-index-core")
    if version != LLAMA_CORE_VERSION:
        raise RagError(f"Expected llama-index-core {LLAMA_CORE_VERSION}, found {version}")
    return Document, TextNode, NodeWithScore, MetadataMode, NodeRelationship, RelatedNodeInfo, QueryBundle, BaseRetriever


def _finite_positive(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise RagError(f"{label} must be a finite positive number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RagError(f"{label} must be a finite positive number") from exc
    if not math.isfinite(number) or number <= 0:
        raise RagError(f"{label} must be a finite positive number")
    return number


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RagError(f"{label} must be a positive integer")
    return value


def load_rag_settings(path: Path) -> RagSettings:
    source = Path(path).resolve(strict=True)
    raw = source.read_bytes()
    data = yaml.safe_load(raw)
    if not isinstance(data, dict) or not isinstance(data.get("rag"), dict):
        raise RagError("RAG config requires a rag mapping")
    root = source.parent.parent.resolve()
    model_path = (root / str(data.get("embedding_config", "configs/smoke.yaml"))).resolve()
    if not model_path.is_relative_to(root):
        raise RagError("Embedding config escapes project root")
    model_config = load_config(model_path)
    rag = data["rag"]
    bm25 = rag.get("bm25")
    fusion = rag.get("fusion")
    if not isinstance(bm25, dict) or not isinstance(fusion, dict):
        raise RagError("RAG config requires bm25 and fusion mappings")
    body_tokens = _positive_int(rag.get("body_tokens"), "rag.body_tokens")
    overlap_tokens = rag.get("overlap_tokens")
    if type(overlap_tokens) is not int or overlap_tokens != 0:
        raise RagError("Initial RAG baseline requires zero overlap")
    k1 = _finite_positive(bm25.get("k1"), "rag.bm25.k1")
    try:
        b = float(bm25.get("b"))
    except (TypeError, ValueError) as exc:
        raise RagError("rag.bm25.b must be in [0,1]") from exc
    if not math.isfinite(b) or not 0 <= b <= 1:
        raise RagError("rag.bm25.b must be in [0,1]")
    top_k = _positive_int(rag.get("top_k"), "rag.top_k")
    branch_top_k = _positive_int(fusion.get("branch_top_k"), "rag.fusion.branch_top_k")
    if branch_top_k < top_k:
        raise RagError("RRF branch_top_k must cover top_k")
    return RagSettings(
        source=source, source_sha256=sha256(raw), root=root,
        model_config=model_config, body_tokens=body_tokens, overlap_tokens=overlap_tokens,
        bm25_k1=k1, bm25_b=b, rrf_k=_positive_int(fusion.get("rrf_k"), "rag.fusion.rrf_k"),
        bm25_weight=_finite_positive(fusion.get("bm25_weight"), "rag.fusion.bm25_weight"),
        dense_weight=_finite_positive(fusion.get("dense_weight"), "rag.fusion.dense_weight"),
        branch_top_k=branch_top_k, top_k=top_k,
    )


class RagChunker:
    """Pinned tokenizer, complete body-token windows, no ONNX session."""

    def __init__(self, settings: RagSettings):
        try:
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise RagError("Pinned tokenizers package is required for RAG chunking") from exc
        model = settings.model_config
        tok_path = model.model_cache / "tokenizer.json"
        config_path = model.model_cache / "config.json"
        pooling_path = model.model_cache / "sentence_bert_config.json"
        for path, expected in ((tok_path, model.tokenizer_sha256),
                               (config_path, model.model_config_sha256),
                               (pooling_path, model.pooling_config_sha256)):
            if not path.is_file() or sha256(path.read_bytes()) != expected:
                raise RagError(f"Pinned tokenizer/model-config file missing or changed: {path.name}")
        model_json = json.loads(config_path.read_text(encoding="utf-8"))
        pooling_json = json.loads(pooling_path.read_text(encoding="utf-8"))
        self.max_input_tokens = min(int(model_json["max_position_embeddings"]),
                                    int(pooling_json["max_seq_length"]))
        self.tokenizer = Tokenizer.from_file(str(tok_path))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.special_count = self.tokenizer.num_special_tokens_to_add(False)
        if self.special_count != 2 or settings.body_tokens + self.special_count > self.max_input_tokens:
            raise RagError("RAG body window plus specials exceeds verified model input limit")
        self.settings = settings

    def chunks(self, text: str) -> list[Chunk]:
        if not isinstance(text, str) or not text.strip():
            raise RagError("RAG input text is empty")
        encoded = self.tokenizer.encode(text, add_special_tokens=False)
        ids, offsets = encoded.ids, encoded.offsets
        if not ids or max(end for _, end in offsets) < len(text.rstrip()) - 2:
            raise RagError("Tokenizer produced no tokens or appears to have truncated input")
        out = []
        size = self.settings.body_tokens
        for start in range(0, len(ids), size):
            end = min(start + size, len(ids))
            spans = [pair for pair in offsets[start:end] if pair[1] > pair[0]]
            if not spans:
                raise RagError("RAG chunk has no source text span")
            a, b = spans[0][0], spans[-1][1]
            if not 0 <= a < b <= len(text):
                raise RagError("Invalid tokenizer source offsets")
            out.append(Chunk(start, end, a, b, tuple(ids[start:end]),
                             sha256(text[a:b].encode("utf-8"))))
        if out[0].token_start != 0 or out[-1].token_end_exclusive != len(ids) or any(
            left.token_end_exclusive != right.token_start for left, right in zip(out, out[1:])
        ):
            raise RagError("Incomplete or overlapping RAG token coverage")
        return out


def _project_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root):
        raise RagError("Input path escapes project root")
    return path


def _binding(root: Path, path: Path, expected: str | None = None) -> dict:
    path = _project_path(root, path)
    if not path.is_file():
        raise RagError(f"Bound input missing: {path.relative_to(root)}")
    actual = sha256(path.read_bytes())
    if expected is not None and actual != expected:
        raise RagError(f"Bound input changed: {path.relative_to(root)}")
    return {"path": path.relative_to(root).as_posix(), "sha256": actual}


def _provenance_context(root: Path, doc: dict) -> tuple[list[dict], str | None]:
    if doc.get("input_type") != "structured_derivative":
        return [], None
    provenance_path = doc.get("provenance_path")
    if not provenance_path or not doc.get("provenance_sha256"):
        raise RagError("Structured derivative lacks verified provenance reference")
    path = _project_path(root, provenance_path)
    if sha256(path.read_bytes()) != doc["provenance_sha256"]:
        raise RagError("Structured provenance bytes changed")
    provenance = json.loads(path.read_text(encoding="utf-8"))
    replacements = provenance.get("replacements")
    if not isinstance(replacements, list) or not replacements:
        raise RagError("Structured provenance lacks table replacements")
    parent = provenance.get("parent_inputs", {}).get("item2_candidate.txt", {})
    parent_path = _project_path(root, parent.get("path", ""))
    if sha256(parent_path.read_bytes()) != parent.get("sha256"):
        raise RagError("Structured parent Item 2 bytes changed")
    return replacements, parent_path.read_text(encoding="utf-8")


def _source_for_chunk(text: str, chunk: Chunk, replacements: list[dict], parent_text: str | None) -> dict:
    a, b = chunk.char_start, chunk.char_end_exclusive
    if not replacements:
        return {"parent_section_unicode_span": [a, b], "table_ids": [],
                "source_mapping": "VERBATIM_ORIGINAL_SECTION"}
    touching = [r for r in replacements if a < r["derivative_unicode_span"][1] and
                b > r["derivative_unicode_span"][0]]
    if touching:
        return {"parent_section_unicode_span": None,
                "table_ids": [r["table_id"] for r in touching],
                "source_table_refs": [{"table_id": r["table_id"],
                                       "parent_section_unicode_span": r["parent_section_unicode_span"],
                                       "source_html_unicode_span": r["source_html_unicode_span"],
                                       "raw_submission_byte_span": r["raw_submission_byte_span"],
                                       "source_html_fragment_sha256": r["source_html_fragment_sha256"]}
                                      for r in touching],
                "source_mapping": "DERIVED_OR_CROSS_TABLE_BOUNDARY"}
    delta = sum((r["parent_section_unicode_span"][1] - r["parent_section_unicode_span"][0]) -
                (r["derivative_unicode_span"][1] - r["derivative_unicode_span"][0])
                for r in replacements if r["derivative_unicode_span"][1] <= a)
    pa, pb = a + delta, b + delta
    if parent_text is None or parent_text[pa:pb] != text[a:b]:
        raise RagError("Non-table derivative chunk does not match parent Item 2")
    return {"parent_section_unicode_span": [pa, pb], "table_ids": [],
            "source_mapping": "EXACT_UNCHANGED_NON_TABLE_SPAN"}


def _node_records(root: Path, doc: dict, chunks: list[Chunk]) -> tuple[dict, list[dict]]:
    Document, TextNode, _, MetadataMode, NodeRelationship, RelatedNodeInfo, _, _ = _llama_types()
    text = doc["text"]
    if sha256(text.encode("utf-8")) != doc["text_sha256"]:
        raise RagError("Loaded text hash mismatch")
    version = str(doc.get("input_version") or doc["text_sha256"])
    doc_seed = "|".join((doc["cik"], doc["accession"], doc["section"],
                         doc.get("input_type", ""), version, doc["text_sha256"]))
    doc_id = "sec:" + sha256(doc_seed.encode("utf-8"))[:32]
    review_references = doc.get("review_references") or []
    clean_refs = []
    if isinstance(review_references, dict):
        review_references = [{"kind": kind, **record} for kind, record in review_references.items()]
    for ref in review_references:
        if isinstance(ref, dict):
            clean_refs.append({"kind": str(ref.get("kind", "review")),
                               "sha256": str(ref.get("sha256", ""))})
    base = {"ticker": doc["ticker"], "cik": doc["cik"],
            "accession": doc["accession"], "form": doc["form"],
            "section": doc["section"], "input_type": doc["input_type"],
            "input_version": version, "text_sha256": doc["text_sha256"],
            "raw_sha256": doc["raw_sha256"],
            "parent_section_sha256": doc.get("parent_section_sha256"),
            "provenance_sha256": doc.get("provenance_sha256"),
            "review_references": clean_refs,
            "limitations": doc.get("limitations") or []}
    excluded = list(base)
    document = Document(text=text, id_=doc_id, metadata=base,
                        excluded_embed_metadata_keys=excluded,
                        excluded_llm_metadata_keys=excluded)
    if document.get_content(metadata_mode=MetadataMode.EMBED) != text:
        raise RagError("Document metadata leaked into embedding text")
    replacements, parent_text = _provenance_context(root, doc)
    records = []
    for i, chunk in enumerate(chunks):
        source = _source_for_chunk(text, chunk, replacements, parent_text)
        node_text = text[chunk.char_start:chunk.char_end_exclusive]
        node_id = f"{doc_id}:c{i:05d}:{chunk.source_span_sha256[:12]}"
        metadata = {**base, "chunk_index": i,
                    "chunk_token_start": chunk.token_start,
                    "chunk_token_end_exclusive": chunk.token_end_exclusive,
                    "chunk_char_start": chunk.char_start,
                    "chunk_char_end_exclusive": chunk.char_end_exclusive,
                    "chunk_text_sha256": chunk.source_span_sha256,
                    "generated_structural_labels": bool(source["table_ids"]),
                    **source}
        node = TextNode(text=node_text, id_=node_id, metadata=metadata,
                        excluded_embed_metadata_keys=list(metadata),
                        excluded_llm_metadata_keys=list(metadata),
                        start_char_idx=chunk.char_start,
                        end_char_idx=chunk.char_end_exclusive,
                        relationships={NodeRelationship.SOURCE: RelatedNodeInfo(node_id=doc_id)})
        if node.get_content(metadata_mode=MetadataMode.EMBED) != node_text or \
                node.get_content(metadata_mode=MetadataMode.LLM) != node_text:
            raise RagError("Node provenance leaked into model content")
        records.append({"node_id": node_id, "doc_id": doc_id, "text": node_text,
                        "metadata": metadata})
    return {"doc_id": doc_id, "identity": base, "node_count": len(records)}, records


def _settings_record(settings: RagSettings) -> dict:
    return {"config_path": settings.source.relative_to(settings.root).as_posix(),
            "config_sha256": settings.source_sha256,
            "embedding_config_path": settings.model_config.source.relative_to(settings.root).as_posix(),
            "embedding_config_sha256": settings.model_config.source_sha256,
            "body_tokens": settings.body_tokens, "overlap_tokens": settings.overlap_tokens,
            "bm25": {"k1": settings.bm25_k1, "b": settings.bm25_b,
                       "tokenizer": TOKENIZER_ID, "lowercase": True,
                       "stopwords": "NONE", "stemming": "NONE",
                       "query_terms": "UNIQUE", "idf": "log1p((N-df+0.5)/(df+0.5))",
                       "length": "ASCII_ALPHANUMERIC_TOKEN_COUNT"},
            "fusion": {"method": "WEIGHTED_RRF", "rrf_k": settings.rrf_k,
                       "bm25_weight": settings.bm25_weight,
                       "dense_weight": settings.dense_weight,
                       "branch_top_k": settings.branch_top_k},
            "top_k": settings.top_k,
            "query_rewriting": "DISABLED"}


def index_documents(spec_paths: list[Path], rag_config_path: Path, output: Path,
                    dense: bool = False) -> dict:
    """Build a new local index from individually validated, exact saved inputs."""
    if not spec_paths:
        raise RagError("At least one validated input spec is required")
    settings = load_rag_settings(rag_config_path)
    _llama_types()
    root = settings.root
    out = Path(output).resolve()
    if not (out.is_relative_to(root / ".cache") or
            out.is_relative_to(root / "artifacts")):
        raise RagError("Local RAG index must be under ignored .cache/ or artifacts/")
    if out.exists():
        raise RagError("Index output exists; use a new directory")
    from .validated_inputs import load_validated_input
    chunker = RagChunker(settings)
    docs, records, chunk_lists = [], [], []
    bindings: dict[str, dict] = {}
    strata, identities = set(), set()
    for spec_path in spec_paths:
        spec = _project_path(root, spec_path)
        doc, audit = load_validated_input(spec, project_root=root)
        if audit.get("validation_status", audit.get("status")) not in (
                "PASS", "PASS_WITH_LIMITATIONS"):
            raise RagError(f"Validated input gate did not pass for {doc['ticker']}")
        key = (doc["cik"], doc["accession"], doc["section"], doc["text_sha256"])
        if key in identities:
            raise RagError("Duplicate validated input version")
        identities.add(key)
        strata.add((doc["form"], doc["section"]))
        bindings[_binding(root, spec)["path"]] = _binding(root, spec)
        validated_files = audit.get("validated_files")
        if not isinstance(validated_files, list) or not validated_files:
            raise RagError("Loader did not provide a complete validated-file binding list")
        for ref in validated_files:
            bound = _binding(root, ref["path"], ref["sha256"])
            bindings[bound["path"]] = bound
        chunks = chunker.chunks(doc["text"])
        descriptor, node_records = _node_records(root, doc, chunks)
        docs.append(descriptor)
        records.extend(node_records)
        chunk_lists.append((doc, chunks))
    if len(strata) != 1:
        raise RagError("RAG index requires one form/section stratum; no 10-K/10-Q pooling")
    if len({r["node_id"] for r in records}) != len(records):
        raise RagError("Duplicate node identifier")
    tokens = [tokenize(r["text"]) for r in records]
    bm25 = BM25(tokens, k1=settings.bm25_k1, b=settings.bm25_b)
    for path in (settings.source, settings.model_config.source,
                 settings.model_config.model_cache / "tokenizer.json",
                 settings.model_config.model_cache / "config.json",
                 settings.model_config.model_cache / "sentence_bert_config.json",
                 root / "src/finembed/rag.py", root / "src/finembed/validated_inputs.py",
                 root / "src/finembed/model.py", root / "src/finembed/lexical.py",
                 root / "src/finembed/rag_embedding.py"):
        bound = _binding(root, path)
        bindings[bound["path"]] = bound
    dense_status = "NOT_BUILT"
    vectors = None
    model_identity = None
    if dense:
        import numpy as np
        from .rag_embedding import PinnedONNXEmbedding
        embedding = PinnedONNXEmbedding(settings.model_config, settings.body_tokens)
        model_identity = {"model_id": embedding.identity["model_id"],
                          "revision": embedding.identity["revision"],
                          "tokenizer_revision": embedding.identity["tokenizer_revision"],
                          "files": {name: item["sha256"] for name, item in embedding.identity["files"].items()},
                          "pooling": "attention-mask mean of last_hidden_state; per-chunk L2",
                          "query_encoding": "same LlamaIndex BaseEmbedding adapter and chunk policy, no query instruction prefix",
                          "llama_index_adapter": "finembed.rag_embedding.PinnedONNXEmbedding"}
        rows = []
        for doc, chunks in chunk_lists:
            encoder_chunks = embedding._encoder.chunks(doc["text"], settings.body_tokens)
            if encoder_chunks != chunks:
                raise RagError("RAG tokenizer chunks differ from verified Encoder chunks")
            rows.extend(embedding.embed_verified_chunks(chunks))
        vectors = np.asarray(rows, dtype=np.float64)
        if vectors.shape[0] != len(records):
            raise RagError("Dense vector row count does not match nodes")
        dense_status = "READY"
        for name in ("onnx/model.onnx",):
            bound = _binding(root, settings.model_config.model_cache / name)
            bindings[bound["path"]] = bound
    out.mkdir(parents=True, exist_ok=False)
    nodes_path = out / "nodes.jsonl"
    with nodes_path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    output_hashes = {"nodes.jsonl": sha256(nodes_path.read_bytes())}
    if vectors is not None:
        import numpy as np
        np.save(out / "vectors.npy", vectors)
        output_hashes["vectors.npy"] = sha256((out / "vectors.npy").read_bytes())
    manifest = {"schema_version": RAG_SCHEMA_VERSION, "kind": "LOCAL_LLAMAINDEX_RAG_INDEX",
                "status": "COMPLETE", "created_utc": datetime.now(timezone.utc).isoformat(),
                "project_root": str(root), "llama_index_core": LLAMA_CORE_VERSION,
                "settings": _settings_record(settings), "stratum": list(next(iter(strata))),
                "documents": docs, "node_count": len(records),
                "bm25_fit": {"corpus": "ALL_VALIDATED_INDEX_NODES_BEFORE_QUERY_FILTERS",
                             "node_count": len(records), "average_length": bm25.avg_length,
                             "vocabulary_size": len(bm25.idf)},
                "dense_status": dense_status, "model_identity": model_identity,
                "input_and_code_bindings": sorted(bindings.values(), key=lambda x: x["path"]),
                "index_file_sha256": output_hashes,
                "review_gate": "LOADER_VERIFIED_PER_INPUT; NO_CROSS_COMPANY_HUMAN_DECISION_INFERRED",
                "financial_experiment": "NOT_RUN", "generation": "NOT_RUN"}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
    return {"status": "COMPLETE", "index_dir": str(out),
            "manifest_sha256": sha256((out / "manifest.json").read_bytes()),
            "documents": len(docs), "nodes": len(records), "dense_status": dense_status,
            "bm25_fit": manifest["bm25_fit"], "model_identity": model_identity}


def _load_index(index_dir: Path) -> tuple[dict, list[dict], RagSettings, Any]:
    out = Path(index_dir).resolve(strict=True)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != RAG_SCHEMA_VERSION or manifest.get("status") != "COMPLETE":
        raise RagError("Unsupported or incomplete local RAG index")
    _llama_types()
    root = Path(manifest["project_root"]).resolve(strict=True)
    settings_path = _project_path(root, manifest["settings"]["config_path"])
    settings = load_rag_settings(settings_path)
    if settings.source_sha256 != manifest["settings"]["config_sha256"] or \
            settings.model_config.source_sha256 != manifest["settings"]["embedding_config_sha256"]:
        raise RagError("RAG or embedding config changed; rebuild index")
    for ref in manifest["input_and_code_bindings"]:
        _binding(root, ref["path"], ref["sha256"])
    for name, expected in manifest["index_file_sha256"].items():
        path = out / name
        if not path.is_file() or sha256(path.read_bytes()) != expected:
            raise RagError(f"Local index file changed: {name}")
    records = [json.loads(line) for line in (out / "nodes.jsonl").read_text(encoding="utf-8").splitlines()]
    if len(records) != manifest["node_count"] or len({x["node_id"] for x in records}) != len(records):
        raise RagError("Local node count or identifiers changed")
    vectors = None
    if manifest["dense_status"] == "READY":
        import numpy as np
        vectors = np.load(out / "vectors.npy", allow_pickle=False)
        if vectors.ndim != 2 or len(vectors) != len(records) or not np.isfinite(vectors).all():
            raise RagError("Invalid saved dense vector matrix")
    return manifest, records, settings, vectors


def _filter_indices(records: list[dict], filters: dict[str, str | None]) -> list[int]:
    allowed = {key: value for key, value in filters.items() if value is not None}
    return [i for i, record in enumerate(records) if all(
        str(record["metadata"].get(key)) == str(value) for key, value in allowed.items())]


def _rank_scores(records: list[dict], indices: list[int], scores: list[float],
                 limit: int, *, positive_only: bool = False) -> list[dict]:
    if any(not math.isfinite(float(scores[i])) for i in indices):
        raise RagError("Nonfinite retrieval score")
    ranked = sorted((i for i in indices if not positive_only or scores[i] > 0),
                    key=lambda i: (-float(scores[i]), records[i]["node_id"]))[:limit]
    return [{"node_id": records[i]["node_id"], "rank": j + 1,
             "score": float(scores[i]), "index": i} for j, i in enumerate(ranked)]


_EMBEDDING_CACHE: dict[tuple[str, str, int], Any] = {}


def _query_vector(query: str, settings: RagSettings):
    import numpy as np
    from .rag_embedding import PinnedONNXEmbedding
    key = (str(settings.model_config.source), settings.model_config.source_sha256,
           settings.body_tokens)
    if key not in _EMBEDDING_CACHE:
        _EMBEDDING_CACHE[key] = PinnedONNXEmbedding(settings.model_config,
                                                   settings.body_tokens)
    v = np.asarray(_EMBEDDING_CACHE[key].get_query_embedding(query), dtype=np.float64)
    if not np.isfinite(v).all():
        raise RagError("Nonfinite dense query vector")
    return v


def _save_query_log(index_dir: Path, query: str, result: dict) -> None:
    log = {"time_utc": datetime.now(timezone.utc).isoformat(),
           "query_sha256": sha256(query.encode("utf-8")), "mode": result["mode"],
           "filters": result["filters"], "status": result["status"],
           "reason": result.get("reason"), "branches": result["branches"],
           "fusion": result["fusion"],
           "results": [{"node_id": hit["node_id"], "rank": hit["rank"],
                        "score": hit["score"], "source": hit["source"]}
                       for hit in result["results"]]}
    with (Path(index_dir) / "query_log.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(log, ensure_ascii=False, sort_keys=True) + "\n")


def _rrf(records: list[dict], branches: dict[str, list[dict]],
         settings: RagSettings, top_k: int) -> list[dict]:
    scores: dict[int, float] = {}
    for name, weight in (("bm25", settings.bm25_weight), ("dense", settings.dense_weight)):
        for hit in branches.get(name, []):
            i = hit["index"]
            scores[i] = scores.get(i, 0.0) + weight / (settings.rrf_k + hit["rank"])
    return _rank_scores(records, list(scores),
                        [scores.get(i, 0.0) for i in range(len(records))], top_k)


def retrieve(index_dir: Path, query: str, mode: str, *, ticker: str | None = None,
             accession: str | None = None, section: str | None = None,
             version: str | None = None, top_k: int | None = None) -> dict:
    """Retrieve with branch-consistent filters; no query rewrite or generation."""
    if mode not in ("bm25", "dense", "hybrid"):
        raise RagError("Mode must be bm25, dense, or hybrid")
    if not isinstance(query, str) or not query.strip():
        raise RagError("Query must contain text")
    manifest, records, settings, vectors = _load_index(index_dir)
    if top_k is not None:
        _positive_int(top_k, "top_k")
        if top_k > settings.branch_top_k:
            raise RagError("top_k exceeds configured branch_top_k")
    else:
        top_k = settings.top_k
    filters = {"ticker": ticker, "accession": accession, "section": section,
               "input_version": version}
    indices = _filter_indices(records, filters)
    manifest_sha = sha256((Path(index_dir) / "manifest.json").read_bytes())
    base = {"mode": mode, "query": query, "query_rewriting": "DISABLED",
            "filters": filters, "index_manifest_sha256": manifest_sha,
            "bm25_fit": manifest["bm25_fit"], "fusion": None,
            "branches": {}, "results": [], "generation": "NOT_RUN"}
    if mode in ("dense", "hybrid") and vectors is None:
        base.update(status="UNAVAILABLE", reason="Dense index was not built; no lexical fallback")
        _save_query_log(index_dir, query, base)
        return base
    if not indices:
        base.update(status="NO_MATCH", reason="No indexed node satisfies every filter")
        _save_query_log(index_dir, query, base)
        return base
    branches: dict[str, list[dict]] = {}
    if mode in ("bm25", "hybrid"):
        query_tokens = tokenize(query)
        if not query_tokens:
            base.update(status="NO_QUERY_TOKENS", reason="ASCII-alphanumeric BM25 tokenizer found no terms")
            _save_query_log(index_dir, query, base)
            return base
        bm = BM25([tokenize(record["text"]) for record in records],
                  k1=settings.bm25_k1, b=settings.bm25_b)
        branches["bm25"] = _rank_scores(records, indices, bm.scores(query_tokens),
                                        settings.branch_top_k, positive_only=True)
    if mode in ("dense", "hybrid"):
        import numpy as np
        q = _query_vector(query, settings)
        scores = (vectors @ q).tolist()
        if any(not math.isfinite(x) for x in scores):
            raise RagError("Nonfinite dense similarity")
        branches["dense"] = _rank_scores(records, indices, scores, settings.branch_top_k)
    if mode == "hybrid":
        ranked = _rrf(records, branches, settings, top_k)
        base["fusion"] = _settings_record(settings)["fusion"]
        score_kind = "WEIGHTED_RRF"
    else:
        ranked = branches[mode][:top_k]
        score_kind = "BM25_DIRECTIONAL" if mode == "bm25" else "DENSE_CHUNK_COSINE"
    Document, TextNode, NodeWithScore, _, NodeRelationship, RelatedNodeInfo, QueryBundle, BaseRetriever = _llama_types()
    node_by_id = {}
    for hit in ranked:
        record = records[hit["index"]]
        node_by_id[record["node_id"]] = TextNode(
            text=record["text"], id_=record["node_id"], metadata=record["metadata"],
            excluded_embed_metadata_keys=list(record["metadata"]),
            excluded_llm_metadata_keys=list(record["metadata"]),
            relationships={NodeRelationship.SOURCE: RelatedNodeInfo(node_id=record["doc_id"])})

    class SavedRankRetriever(BaseRetriever):
        def _retrieve(self, query_bundle: QueryBundle):
            if query_bundle.query_str != query:
                raise RagError("Unexpected query rewrite")
            return [NodeWithScore(node=node_by_id[hit["node_id"]], score=hit["score"])
                    for hit in ranked]

    llama_hits = SavedRankRetriever().retrieve(query)
    if [hit.node.node_id for hit in llama_hits] != [hit["node_id"] for hit in ranked]:
        raise RagError("LlamaIndex retriever changed ranking or node IDs")
    base["branches"] = {name: [{k: hit[k] for k in ("node_id", "rank", "score")}
                                for hit in hits] for name, hits in branches.items()}
    for rank, hit in enumerate(ranked, 1):
        record = records[hit["index"]]
        meta = record["metadata"]
        base["results"].append({"node_id": record["node_id"], "rank": rank,
                                "score": hit["score"], "score_kind": score_kind,
                                "text": record["text"],
                                "source": meta.copy()})
    base["status"] = "OK" if base["results"] else "NO_MATCH"
    if not base["results"]:
        base["reason"] = "No positive BM25 match and no dense candidate"
    _save_query_log(index_dir, query, base)
    return base


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("index", help="Build a new local index from validated input specs")
    build.add_argument("--spec", type=Path, action="append", required=True)
    build.add_argument("--config", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--dense", action="store_true")
    search = sub.add_parser("retrieve", help="Retrieve source-bound evidence without generation")
    search.add_argument("--index", type=Path, required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--mode", choices=("bm25", "dense", "hybrid"), required=True)
    for flag in ("ticker", "accession", "section", "version"):
        search.add_argument("--" + flag)
    search.add_argument("--top-k", type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "index":
            result = index_documents(args.spec, args.config, args.output, dense=args.dense)
        else:
            result = retrieve(args.index, args.query, args.mode,
                              ticker=args.ticker, accession=args.accession,
                              section=args.section, version=args.version, top_k=args.top_k)
    except (RagError, ValueError, FileNotFoundError, KeyError) as exc:
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__, "reason": str(exc)}),
              file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
