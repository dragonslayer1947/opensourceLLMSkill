from devagent.knowledge import service_registry as sr


def _write(root, name, body):
    d = sr.registry_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.yaml").write_text(body, encoding="utf-8")


def test_sample_roundtrip(tmp_path):
    p = sr.write_sample(tmp_path)
    assert p.exists()
    svcs = sr.load_services(tmp_path)
    assert "checkout-service" in svcs
    s = svcs["checkout-service"]
    assert s.sla_tier == "critical" and "pci-dss" in s.compliance_zones
    assert "inventory-service" in s.consumes_names
    assert "orders" in s.dbs_owned


def test_downstream_consumers(tmp_path):
    _write(tmp_path, "inventory", "name: inventory\n")
    _write(tmp_path, "checkout", "name: checkout\napis:\n  consumes:\n    - service: inventory\n")
    _write(tmp_path, "search", "name: search\napis:\n  consumes:\n    - service: inventory\n")
    svcs = sr.load_services(tmp_path)
    assert sr.downstream_consumers(svcs, "inventory") == ["checkout", "search"]
    assert sr.downstream_consumers(svcs, "checkout") == []


def test_load_missing_returns_empty(tmp_path):
    assert sr.load_services(tmp_path) == {}
