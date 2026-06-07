"""Optional semantic-retrieval tier (gap #4).

Lexical ranking (rag.py BM25) is fast and deterministic but recall-limited: on a 100k-LOC repo
the file you need may share *no* keywords with the task ("charge the customer" vs. a file full of
`Invoice`/`Stripe`/`settle`). An embedding tier fixes that — it ranks by meaning, so the right
slice still reaches the local executor at scale.

Design constraints kept from the rest of devagent:
- **Offline-safe & opt-in.** No embeddings model configured (no `embedder` role) or the endpoint
  unreachable → every call here returns None and retrieval falls back to pure lexical ranking,
  byte-for-byte unchanged. Determinism offline is preserved.
- **Scales by caching.** File vectors are computed once and stored in the index cache (keyed by
  the same fingerprint), so a query embeds ONE string and does O(n) dot products — not a re-embed
  of the repo per search.
- **No new dependency.** Talks to any OpenAI-compatible `/v1/embeddings` (llama.cpp `--embeddings`,
  Ollama, or a metered API) over stdlib urllib."""
from __future__ import annotations

import json
import math
import urllib.request


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class Embedder:
    """Thin client for an OpenAI-compatible embeddings endpoint. Every method is failure-tolerant:
    any network/parse error returns None so the caller transparently falls back to lexical."""

    def __init__(self, base_url: str, model_id: str, api_key: str | None = None, timeout: int = 30):
        self.url = base_url.rstrip("/") + "/embeddings"
        self.model_id = model_id
        self.api_key = api_key
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not texts:
            return []
        try:
            body = json.dumps({"model": self.model_id, "input": texts}).encode("utf-8")
            req = urllib.request.Request(
                self.url, data=body, headers={"Content-Type": "application/json"})
            if self.api_key:
                req.add_header("Authorization", f"Bearer {self.api_key}")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            out = [d.get("embedding") for d in data.get("data", [])]
            return out if len(out) == len(texts) and all(out) else None
        except Exception:
            return None

    def embed_one(self, text: str) -> list[float] | None:
        got = self.embed([text])
        return got[0] if got else None


def get_embedder(config) -> Embedder | None:
    """Build an Embedder from the `embedder` role chain, or None if unconfigured.

    Opt-in: add `embedder = ["my-embed-model"]` under [roles] and declare that model. Absent → None
    → lexical-only retrieval (the default, fully offline)."""
    try:
        names = config.roles.get("embedder") or []
    except AttributeError:
        return None
    for name in names:
        spec = config.models.get(name)
        if spec and spec.base_url:
            return Embedder(spec.base_url, spec.model_id, spec.api_key, spec.timeout_s)
    return None


def embed_text_for_file(entry) -> str:
    """The text we embed for a file: its path + symbol signatures (a compact semantic fingerprint
    that captures what the file is about without sending the whole body to the endpoint)."""
    parts = [entry.rel.replace("/", " ")]
    for s in getattr(entry, "symbols", [])[:40]:
        parts.append(s.signature or s.name)
    return "\n".join(parts)


def attach_embeddings(index, embedder: Embedder | None, *, batch: int = 64) -> bool:
    """Populate `entry.vector` for files missing one (cache hits keep theirs). Returns True if any
    vector was computed. Best-effort: a failed batch leaves those files vectorless (lexical-only)."""
    if embedder is None:
        return False
    pending = [f for f in index.files if f.vector is None]
    changed = False
    for i in range(0, len(pending), batch):
        chunk = pending[i:i + batch]
        vecs = embedder.embed([embed_text_for_file(f) for f in chunk])
        if not vecs:
            continue
        for f, v in zip(chunk, vecs):
            f.vector = v
            changed = True
    return changed
