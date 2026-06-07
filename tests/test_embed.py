"""Semantic retrieval tier (gap #4). Uses a deterministic fake embedder (no network), so the
RANKING mechanism is proven; whether it activates in production depends only on configuring an
embeddings endpoint. Offline (no embedder) behaviour must stay byte-for-byte unchanged."""
import json

from devagent.context import rag
from devagent.context.cache import CACHE_FILE, build_index_cached
from devagent.context.embed import attach_embeddings, cosine
from devagent.context.index import build_index

# Concept axes: [billing, auth, data]. A text's vector counts concept-word hits, so two texts
# about the same concept score high cosine even with ZERO shared tokens — i.e. real "meaning".
_CONCEPTS = [
    ("charge", "customer", "invoice", "stripe", "payment", "settle", "bill"),
    ("login", "token", "auth", "password", "session"),
    ("parse", "csv", "file", "read", "load"),
]


class _FakeEmbedder:
    def embed(self, texts):
        out = []
        for t in texts:
            low = t.lower()
            out.append([float(sum(low.count(w) for w in group)) for group in _CONCEPTS])
        return out

    def embed_one(self, text):
        return self.embed([text])[0]


def test_cosine_basic():
    assert cosine([1, 0], [1, 0]) == 1.0
    assert cosine([1, 0], [0, 1]) == 0.0
    assert cosine(None, [1]) == 0.0


def _w(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_semantic_tier_surfaces_keywordless_match(tmp_path):
    # billing file shares NO tokens with the query "charge the customer"
    _w(tmp_path, "app/money.py", "def settle_invoice():\n    return 'stripe'\n")
    _w(tmp_path, "app/access.py", "def login(token):\n    return token\n")
    idx = build_index(tmp_path)
    emb = _FakeEmbedder()
    fv = {e.rel: emb.embed_one(_text(e)) for e in idx.files}
    qv = emb.embed_one("charge the customer")

    # Lexical only: query terms appear in neither file -> no match.
    assert rag.rank_files(idx, "charge the customer") == []
    # Semantic tier: the billing file is surfaced by MEANING.
    ranked = rag.rank_files(idx, "charge the customer", file_vectors=fv, query_vector=qv)
    assert ranked and ranked[0] == "app/money.py"
    assert "app/access.py" not in ranked  # zero cosine -> not added


def _text(entry):
    from devagent.context.embed import embed_text_for_file
    return embed_text_for_file(entry)


def test_offline_unchanged_when_no_vectors(tmp_repo):
    idx = build_index(tmp_repo)
    before = rag.rank_files(idx, "improve the total sum")
    after = rag.rank_files(idx, "improve the total sum", file_vectors=None, query_vector=None)
    assert before == after and before  # matches calc.py, and identical with/without the kwargs


def test_attach_embeddings_and_cache_roundtrip(tmp_path):
    _w(tmp_path, "app/money.py", "def settle_invoice():\n    return 1\n")
    # build with embedder -> vectors computed + persisted
    idx = build_index_cached(tmp_path, embedder=_FakeEmbedder())
    assert all(f.vector is not None for f in idx.files)
    cache = json.loads((tmp_path / CACHE_FILE).read_text(encoding="utf-8"))
    assert cache["files"][0]["vector"] is not None
    # rebuild from cache -> vectors survive (no recompute needed)
    idx2 = build_index_cached(tmp_path, embedder=_FakeEmbedder())
    assert idx2.files[0].vector == idx.files[0].vector


def test_attach_embeddings_noop_without_embedder(tmp_path):
    _w(tmp_path, "app/x.py", "def f():\n    return 1\n")
    idx = build_index(tmp_path)
    assert attach_embeddings(idx, None) is False
    assert all(f.vector is None for f in idx.files)
