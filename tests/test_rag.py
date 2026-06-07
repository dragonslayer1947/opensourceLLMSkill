from devagent.context.index import build_index
from devagent.context.rag import rank_files, tokenize
from devagent.planning.blast_radius import build_dependents


def test_tokenize_splits_snake_and_camel():
    toks = set(tokenize("list_products getUser"))
    assert {"list", "products", "user"} <= toks


def _repo(tmp_path):
    (tmp_path / "orders.py").write_text(
        "def create_order(cart):\n    return cart\n", encoding="utf-8")
    (tmp_path / "pricing.py").write_text(
        "def compute_discount(total):\n    return total * 0.9\n", encoding="utf-8")
    return tmp_path


def test_exact_symbol_match_ranks_first(tmp_path):
    idx = build_index(_repo(tmp_path))
    ranked = rank_files(idx, "fix compute_discount rounding")
    assert ranked and ranked[0] == "pricing.py"


def test_no_match_returns_empty(tmp_path):
    idx = build_index(_repo(tmp_path))
    assert rank_files(idx, "zzz qqq nonexistent") == []


def test_graph_tier_pulls_in_dependents(tmp_path):
    (tmp_path / "core.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    (tmp_path / "user.py").write_text(
        "from core import base\n\ndef use():\n    return base()\n", encoding="utf-8")
    idx = build_index(tmp_path)
    deps = build_dependents(idx)
    # query hits core.py by symbol; graph tier should also surface its dependent user.py
    ranked = rank_files(idx, "change base", dependents=deps, limit=10)
    assert "core.py" in ranked and "user.py" in ranked


def test_empty_query_returns_empty(tmp_path):
    idx = build_index(_repo(tmp_path))
    assert rank_files(idx, "the a to of") == []  # all stopwords
