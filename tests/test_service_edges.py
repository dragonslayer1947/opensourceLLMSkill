"""Cross-service runtime edges (gap #3): HTTP route + pub/sub coupling that the import graph
can't see must still land in the blast radius and impacted-test selection."""
from devagent.context.index import build_index
from devagent.planning import blast_radius, service_edges
from devagent.validate import impact


def _w(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_index_extracts_routes_and_topics(tmp_path):
    _w(tmp_path, "svc/api.py",
       "from fastapi import APIRouter\nrouter = APIRouter()\n\n"
       "@router.get('/services/{id}')\ndef get_service(id):\n    return id\n")
    _w(tmp_path, "client/caller.py",
       "import httpx\n\ndef fetch():\n    return httpx.get('http://api/services/42')\n")
    idx = build_index(tmp_path)
    by = {f.rel: f for f in idx.files}
    assert "services" in by["svc/api.py"].routes_defined
    assert "services" in by["client/caller.py"].routes_used


def test_route_edge_in_blast_radius(tmp_path):
    # No import between them — only an HTTP call couples caller -> server.
    _w(tmp_path, "svc/api.py",
       "from fastapi import APIRouter\nrouter = APIRouter()\n\n"
       "@router.post('/bookings')\ndef create_booking():\n    return {}\n")
    _w(tmp_path, "tests/test_bookings.py",
       "from starlette.testclient import TestClient\n\n"
       "def test_create(client):\n    r = client.post('/bookings', json={})\n    assert r\n")
    idx = build_index(tmp_path)
    deps = blast_radius.build_dependents(idx)
    # changing the route definer must mark the caller/test as a dependent
    assert "tests/test_bookings.py" in deps["svc/api.py"]
    # and impacted-test selection must pick that test up
    selected = impact.select_impacted_tests(idx, ["svc/api.py"])
    assert "tests/test_bookings.py" in selected


def test_topic_edge_is_bidirectional(tmp_path):
    _w(tmp_path, "producer.py",
       "def emit(bus):\n    bus.publish('order.created', {})\n")
    _w(tmp_path, "consumer.py",
       "def listen(bus):\n    bus.subscribe('order.created', handler=None)\n")
    idx = build_index(tmp_path)
    extra = service_edges.runtime_dependents(idx)
    assert "consumer.py" in extra.get("producer.py", set())
    assert "producer.py" in extra.get("consumer.py", set())


def test_no_false_edges_without_signals(tmp_path):
    _w(tmp_path, "a.py", "def f():\n    return 1\n")
    _w(tmp_path, "b.py", "def g():\n    return 2\n")
    idx = build_index(tmp_path)
    assert service_edges.runtime_dependents(idx) == {}


def test_route_segments_normalizes():
    from devagent.context.index import _route_segments
    assert _route_segments("/services/{id}") == {"services"}
    assert _route_segments("/services/42") == {"services"}
    assert _route_segments("http://api.host/v1/orders?x=1") == {"v1", "orders"}
    assert _route_segments("/") == set()
    # prefix-mount case: handler path vs. full caller path overlap on the shared static segment
    assert _route_segments("/{id}/cancel") & _route_segments("/bookings/7/cancel") == {"cancel"}


def test_prefix_mounted_route_links(tmp_path):
    # server handler decorated WITHOUT the prefix (added at include_router time)…
    _w(tmp_path, "svc/bookings_api.py",
       "from fastapi import APIRouter\nrouter = APIRouter()\n\n"
       "@router.post('/{booking_id}/cancel')\ndef cancel(booking_id):\n    return {}\n")
    # …caller hits the full mounted path
    _w(tmp_path, "tests/test_cancel.py",
       "def test_cancel(client):\n    assert client.post('/bookings/7/cancel')\n")
    idx = build_index(tmp_path)
    assert "tests/test_cancel.py" in blast_radius.build_dependents(idx)["svc/bookings_api.py"]
