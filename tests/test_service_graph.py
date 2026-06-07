from devagent.knowledge import service_graph as sg
from devagent.knowledge import service_registry as sr


def _svcs(tmp_path, defs):
    d = sr.registry_dir(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    for name, body in defs.items():
        (d / f"{name}.yaml").write_text(body, encoding="utf-8")
    return sr.load_services(tmp_path)


def test_transitive_downstream(tmp_path):
    svcs = _svcs(tmp_path, {
        "inventory": "name: inventory\n",
        "checkout": "name: checkout\napis:\n  consumes:\n    - service: inventory\n",
        "web": "name: web\napis:\n  consumes:\n    - service: checkout\n",
        "lonely": "name: lonely\n",
    })
    # changing inventory transitively affects checkout (direct) and web (via checkout)
    assert sg.transitive_downstream(svcs, "inventory") == {"checkout", "web"}
    assert sg.transitive_downstream(svcs, "web") == set()


def test_no_cycle_blowup(tmp_path):
    svcs = _svcs(tmp_path, {
        "a": "name: a\napis:\n  consumes:\n    - service: b\n",
        "b": "name: b\napis:\n  consumes:\n    - service: a\n",
    })
    assert sg.transitive_downstream(svcs, "a") == {"b"}


def test_service_for_path(tmp_path):
    svcs = _svcs(tmp_path, {
        "checkout": "name: checkout\nroot: services/checkout\n",
        "inventory": "name: inventory\nroot: services/inventory\n",
    })
    assert sg.service_for_path(svcs, "services/checkout/app/main.py") == "checkout"
    assert sg.service_for_path(svcs, "services/inventory/db.py") == "inventory"
    assert sg.service_for_path(svcs, "tooling/build.py") is None


def test_services_for_paths(tmp_path):
    svcs = _svcs(tmp_path, {
        "checkout": "name: checkout\nroot: services/checkout\n",
    })
    got = sg.services_for_paths(svcs, ["services/checkout/a.py", "services/checkout/b.py", "x/y.py"])
    assert got == {"checkout"}
