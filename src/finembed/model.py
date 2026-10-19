"""Pinned ONNX pretrained encoder and exact source-token mapping."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

from .config import Config
from .run import sha256


class ModelError(RuntimeError):
    pass


MODEL_FILES = {
    "onnx/model.onnx": "onnx_sha256",
    "tokenizer.json": "tokenizer_sha256",
    "config.json": "model_config_sha256",
    "sentence_bert_config.json": "pooling_config_sha256",
}


def verify_model(config: Config) -> dict:
    files = {}
    for name, field in MODEL_FILES.items():
        path = config.model_cache / name
        if not path.is_file():
            raise ModelError(f"Pinned model file missing: {path}")
        digest = sha256(path.read_bytes())
        if digest != getattr(config, field):
            raise ModelError(f"Pinned model file hash mismatch: {path}")
        files[name] = {"path": str(path), "sha256": digest, "bytes": path.stat().st_size}
    model_config = json.loads((config.model_cache / "config.json").read_text(encoding="utf-8"))
    pooling = json.loads((config.model_cache / "sentence_bert_config.json").read_text(encoding="utf-8"))
    max_length = min(int(model_config["max_position_embeddings"]), int(pooling["max_seq_length"]))
    if max_length < 194:
        raise ModelError(f"Model input limit {max_length} cannot fit 192 body tokens plus specials")
    return {"model_id": config.model_id, "revision": config.model_revision,
            "tokenizer_revision": config.tokenizer_revision, "max_input_tokens": max_length, "files": files}


def fetch_model(config: Config) -> dict:
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(config.model_id, revision=config.model_revision,
                          allow_patterns=list(MODEL_FILES), local_dir=config.model_cache)
    except Exception as exc:
        raise ModelError(f"Pinned model download failed: {type(exc).__name__}: {exc}") from exc
    return verify_model(config)


@dataclass(frozen=True)
class Chunk:
    token_start: int
    token_end_exclusive: int
    char_start: int
    char_end_exclusive: int
    token_ids: tuple[int, ...]
    source_span_sha256: str


class Encoder:
    def __init__(self, config: Config):
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise ModelError("Install the embed optional dependencies: python -m pip install -e '.[embed,test]'") from exc
        self.config = config
        self.identity = verify_model(config)
        self.tokenizer = Tokenizer.from_file(str(config.model_cache / "tokenizer.json"))
        # The published tokenizer.json enables 128-token truncation/padding.
        # Disable both before whole-document tokenization or later text would disappear.
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.cls = self.tokenizer.token_to_id("[CLS]")
        self.sep = self.tokenizer.token_to_id("[SEP]")
        self.pad = self.tokenizer.token_to_id("[PAD]")
        special_count = self.tokenizer.num_special_tokens_to_add(False)
        if None in (self.cls, self.sep, self.pad) or special_count != 2:
            raise ModelError("Pinned tokenizer does not provide expected BERT special tokens")
        self.special_count = special_count
        self.identity["special_tokens_per_single_chunk"] = special_count
        self.session = ort.InferenceSession(str(config.model_cache / config.onnx_file),
                                            providers=[config.provider])
        if {x.name for x in self.session.get_inputs()} != {"input_ids", "attention_mask", "token_type_ids"}:
            raise ModelError("Unexpected ONNX model input interface")
        if self.session.get_outputs()[0].name != "last_hidden_state":
            raise ModelError("Unexpected ONNX model output interface")

    def chunks(self, text: str, body_tokens: int) -> list[Chunk]:
        if body_tokens + self.special_count > self.identity["max_input_tokens"]:
            raise ModelError("Chunk plus special tokens exceeds model input limit")
        encoded = self.tokenizer.encode(text, add_special_tokens=False)
        ids, offsets = encoded.ids, encoded.offsets
        if not ids:
            raise ModelError("No body tokens in Item 1")
        if max(end for _, end in offsets) < len(text.rstrip()) - 2:
            raise ModelError("Tokenizer appears to have truncated the document")
        chunks = []
        for start in range(0, len(ids), body_tokens):
            end = min(start + body_tokens, len(ids))
            span_offsets = [pair for pair in offsets[start:end] if pair[1] > pair[0]]
            if not span_offsets:
                raise ModelError("Chunk has no nonempty source offsets")
            char_start, char_end = span_offsets[0][0], span_offsets[-1][1]
            if not 0 <= char_start < char_end <= len(text):
                raise ModelError("Tokenizer source offsets are invalid")
            chunks.append(Chunk(start, end, char_start, char_end, tuple(ids[start:end]),
                                sha256(text[char_start:char_end].encode("utf-8"))))
        if chunks[0].token_start != 0 or chunks[-1].token_end_exclusive != len(ids) or any(
            a.token_end_exclusive != b.token_start for a, b in zip(chunks, chunks[1:])
        ):
            raise ModelError("Chunk coverage is incomplete or overlapping")
        return chunks

    def embed_chunks(self, chunks: list[Chunk]):
        import numpy as np
        rows = []
        for offset in range(0, len(chunks), self.config.batch_size):
            batch = chunks[offset:offset + self.config.batch_size]
            sequences = [[self.cls, *c.token_ids, self.sep] for c in batch]
            if any(len(s) > self.identity["max_input_tokens"] for s in sequences):
                raise ModelError("Actual input including specials exceeds model limit")
            width = max(map(len, sequences))
            input_ids = np.full((len(batch), width), self.pad, dtype=np.int64)
            mask = np.zeros((len(batch), width), dtype=np.int64)
            for i, seq in enumerate(sequences):
                input_ids[i, :len(seq)] = seq
                mask[i, :len(seq)] = 1
            output = self.session.run(None, {"input_ids": input_ids, "attention_mask": mask,
                                             "token_type_ids": np.zeros_like(input_ids)})[0]
            weight = mask[:, :, None].astype(np.float64)
            pooled = (output.astype(np.float64) * weight).sum(axis=1) / weight.sum(axis=1)
            if not np.isfinite(pooled).all():
                raise ModelError("Nonfinite chunk embedding")
            rows.append(pooled)
        return np.vstack(rows)


def aggregate(chunk_vectors, normalize_chunks: bool, normalize_documents: bool):
    import numpy as np
    v = np.asarray(chunk_vectors, dtype=np.float64)
    if v.ndim != 2 or v.shape[0] == 0 or not np.isfinite(v).all():
        raise ModelError("Invalid chunk vector matrix")
    if normalize_chunks:
        norms = np.linalg.norm(v, axis=1, keepdims=True)
        if (norms <= 0).any():
            raise ModelError("Zero norm chunk vector")
        v = v / norms
    doc = v.mean(axis=0)
    if normalize_documents:
        norm = np.linalg.norm(doc)
        if not np.isfinite(norm) or norm <= 0:
            raise ModelError("Zero or nonfinite document vector")
        doc = doc / norm
    return doc

