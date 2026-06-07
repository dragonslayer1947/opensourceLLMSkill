from devagent.execute.contract import diff_openapi, wrap_skeleton


def _doc(paths):
    return wrap_skeleton(paths)


def test_path_removed_is_breaking():
    old = _doc({"/a": {"get": {"responses": {"200": {"description": "ok"}}}}})
    new = _doc({})
    changes = diff_openapi(old, new)
    assert any(c.kind == "path_removed" and c.location == "/a" for c in changes)


def test_method_removed_is_breaking():
    old = _doc({"/a": {"get": {}, "post": {}}})
    new = _doc({"/a": {"get": {}}})
    changes = diff_openapi(old, new)
    assert any(c.kind == "method_removed" and "POST /a" in c.location for c in changes)


def test_new_required_request_field_is_breaking():
    sch = lambda req: {"requestBody": {"content": {"application/json": {  # noqa: E731
        "schema": {"type": "object", "required": req, "properties": {"a": {}, "b": {}}}}}}}
    old = _doc({"/a": {"post": sch(["a"])}})
    new = _doc({"/a": {"post": sch(["a", "b"])}})
    changes = diff_openapi(old, new)
    assert any(c.kind == "request_required_added" and "b" in c.detail for c in changes)


def test_removed_response_field_is_breaking():
    def resp(props):
        return {"responses": {"200": {"content": {"application/json": {
            "schema": {"type": "object", "properties": props}}}}}}
    old = _doc({"/a": {"get": resp({"id": {"type": "string"}, "name": {"type": "string"}})}})
    new = _doc({"/a": {"get": resp({"id": {"type": "string"}})}})
    changes = diff_openapi(old, new)
    assert any(c.kind == "response_field_removed" and "name" in c.detail for c in changes)


def test_field_type_change_is_breaking():
    def resp(t):
        return {"responses": {"200": {"content": {"application/json": {
            "schema": {"type": "object", "properties": {"id": {"type": t}}}}}}}}
    old = _doc({"/a": {"get": resp("string")}})
    new = _doc({"/a": {"get": resp("integer")}})
    changes = diff_openapi(old, new)
    assert any(c.kind == "field_type_changed" and "string → integer" in c.detail for c in changes)


def test_additive_change_is_not_breaking():
    old = _doc({"/a": {"get": {"responses": {"200": {"description": "ok"}}}}})
    new = _doc({"/a": {"get": {"responses": {"200": {"description": "ok"}}},
                       "/b": {}}})  # new path added
    new["paths"]["/b"] = {"get": {}}
    assert diff_openapi(old, new) == []
