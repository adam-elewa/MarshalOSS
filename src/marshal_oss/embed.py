from __future__ import annotations

import hashlib
import os
import struct
from typing import Sequence

import numpy as np

from .config import Settings


class Embedder:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.backend = settings.embed_backend
        self.model = settings.embed_model
        self._voyage = None
        key = settings.voyage_api_key or os.environ.get("VOYAGE_API_KEY")
        if self.backend == "voyage":
            if not key:
                self.backend = "hash"
            else:
                import voyageai

                self._voyage = voyageai.Client(api_key=key)

    @property
    def model_id(self) -> str:
        return self.model if self.backend == "voyage" else "hash-384"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.backend == "voyage" and self._voyage is not None:
            # Voyage batch limit ~128
            out: list[list[float]] = []
            batch_size = 64
            for i in range(0, len(texts), batch_size):
                batch = list(texts[i : i + batch_size])
                resp = self._voyage.embed(batch, model=self.model, input_type="document")
                out.extend([list(v) for v in resp.embeddings])
            return out
        return [self._hash_embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        if self.backend == "voyage" and self._voyage is not None:
            resp = self._voyage.embed([text], model=self.model, input_type="query")
            return list(resp.embeddings[0])
        return self._hash_embed(text)

    def _hash_embed(self, text: str, dim: int = 384) -> list[float]:
        """Deterministic bag-of-hashes embedding for offline / no-key mode."""
        vec = np.zeros(dim, dtype=np.float32)
        for tok in text.lower().split():
            h = hashlib.sha256(tok.encode("utf-8")).digest()
            idx = struct.unpack_from(">I", h, 0)[0] % dim
            sign = 1.0 if h[4] % 2 == 0 else -1.0
            vec[idx] += sign
        n = float(np.linalg.norm(vec))
        if n > 0:
            vec /= n
        return vec.tolist()


def cosine_topk(
    query: list[float],
    corpus: list[tuple[int, list[float], dict]],
    k: int = 20,
) -> list[tuple[float, dict]]:
    if not corpus:
        return []
    q = np.asarray(query, dtype=np.float32)
    qn = float(np.linalg.norm(q)) or 1.0
    q = q / qn
    qdim = int(q.shape[0])
    scored: list[tuple[float, dict]] = []
    for _cid, vec, meta in corpus:
        v = np.asarray(vec, dtype=np.float32)
        if int(v.shape[0]) != qdim:
            # Skip stale/mismatched embeddings (e.g. hash-384 vs voyage-1024)
            continue
        vn = float(np.linalg.norm(v)) or 1.0
        score = float(np.dot(q, v / vn))
        scored.append((score, meta))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:k]
