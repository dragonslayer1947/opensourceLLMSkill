from devagent.execute.contract import (
    conformance_check,
    extract_spec_block,
    is_api_task,
    validate_openapi,
    wrap_skeleton,
)

VALID_PATHS = {
    "/products": {
        "get": {
            "responses": {"200": {"description": "ok"}},
        },
        "post": {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {"type": "object", "required": ["name"],
                                   "properties": {"name": {"type": "string"}}}
                    }
                }
            },
            "responses": {"201": {"description": "created"}},
        },
    }
}


def test_is_api_task():
    assert is_api_task("add a paginated products endpoint")
    assert is_api_task("define the REST route for checkout")
    assert not is_api_task("rename a helper function")


def test_validate_valid_spec():
    ok, errors = validate_openapi(wrap_skeleton(VALID_PATHS))
    assert ok and errors == []


def test_validate_invalid_spec():
    ok, errors = validate_openapi({"openapi": "3.0.3"})  # missing info/paths
    assert not ok and errors


def test_extract_spec_block_fenced():
    text = "here:\n```yaml\n/x:\n  get:\n    responses:\n      '200':\n        description: ok\n```\n"
    data = extract_spec_block(text)
    assert isinstance(data, dict) and "/x" in data


def test_conformance_detects_missing_path():
    doc = wrap_skeleton(VALID_PATHS)
    code = "def handler():\n    return []\n"  # no /products
    d = conformance_check(doc, code)
    assert any("/products" in x for x in d)


def test_conformance_detects_missing_field():
    doc = wrap_skeleton(VALID_PATHS)
    code = "@app.get('/products')\n@app.post('/products')\ndef p():\n    return 1\n"  # 'name' missing
    d = conformance_check(doc, code)
    assert any("name" in x for x in d)


def test_conformance_passes_when_present():
    doc = wrap_skeleton(VALID_PATHS)
    code = "routes = ['/products']\nclass Body:\n    name: str\n"
    assert conformance_check(doc, code) == []
