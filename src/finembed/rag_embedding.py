"""LlamaIndex embedding adapter for the project's hash-pinned local ONNX encoder."""
from __future__ import annotations

import numpy as np
from llama_index.core.base.embeddings.base import BaseEmbedding
from pydantic import PrivateAttr

from .config import Config
from .model import Chunk, Encoder, aggregate


class PinnedONNXEmbedding(BaseEmbedding):
    """Embed saved token chunks and queries with one verified local model.

    Pre-tokenized source chunks use :meth:`embed_verified_chunks` to preserve
    their full-document token IDs and original-character offsets. LlamaIndex's
    ordinary text/query methods apply the separate RAG chunk policy to a new
    string and aggregate its normalized chunks. No remote model is involved.
    """

    _encoder: Encoder = PrivateAttr()
    _body_tokens: int = PrivateAttr()

    def __init__(self, config: Config, body_tokens: int):
        super().__init__(model_name=f"{config.model_id}@{config.model_revision}")
        self._encoder = Encoder(config)
        self._body_tokens = body_tokens

    @property
    def identity(self) -> dict:
        return self._encoder.identity

    def embed_verified_chunks(self, chunks: list[Chunk]) -> np.ndarray:
        """L2-normalize each exact source-token chunk without retokenizing it."""
        if not chunks:
            raise ValueError("No source chunks to embed")
        vectors = self._encoder.embed_chunks(chunks)
        norms = np.linalg.norm(vectors, axis=1)
        if not np.isfinite(vectors).all() or not np.isfinite(norms).all() or (norms <= 0).any():
            raise ValueError("Invalid pinned-encoder chunk vector")
        return vectors / norms[:, None]

    def _embed_text(self, text: str) -> list[float]:
        chunks = self._encoder.chunks(text, self._body_tokens)
        vectors = self._encoder.embed_chunks(chunks)
        result = aggregate(vectors, normalize_chunks=True, normalize_documents=True)
        return result.tolist()

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._embed_text(query)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._embed_text(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._embed_text(text)
