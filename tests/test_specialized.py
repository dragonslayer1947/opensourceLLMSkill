from devagent.execute.specialized import DOMAIN_GUIDANCE, detect_domain, guidance_for


def test_migration_by_path():
    assert detect_domain("tweak", ["db/migrations/0007_add.py"]) == "migration"


def test_migration_by_text():
    assert detect_domain("add a column to the orders table", []) == "migration"


def test_infra_by_suffix():
    assert detect_domain("scale up", ["infra/main.tf"]) == "infra"


def test_infra_by_text():
    assert detect_domain("update the kubernetes deployment manifest", []) == "infra"


def test_frontend_by_suffix():
    assert detect_domain("style it", ["web/Button.tsx"]) == "frontend"


def test_api_by_text():
    assert detect_domain("add a GET endpoint", ["svc/routes.py"]) == "api"


def test_default_backend():
    assert detect_domain("refactor the pricing calculator", ["svc/pricing.py"]) == "backend"


def test_guidance_for_returns_text_for_known_domains():
    domain, text = guidance_for("add a column to users", [])
    assert domain == "migration" and text == DOMAIN_GUIDANCE["migration"]
    domain, text = guidance_for("refactor helper", ["a.py"])
    assert domain == "backend" and text == ""
