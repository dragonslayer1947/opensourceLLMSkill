import json

from devagent.integrations import registry, sync
from devagent.integrations.base import NullProvider, Provider
from devagent.longhorizon import epic as epic_mod

PLAN = {
    "title": "feature",
    "stories": [
        {"id": "S1", "title": "story one", "tasks": [{"id": "T1", "title": "t", "target_files": ["a.py"]}]},
        {"id": "S2", "title": "story two", "tasks": [{"id": "T2", "title": "u", "target_files": ["b.py"]}]},
    ],
}


def test_null_provider_satisfies_protocol(tmp_path):
    prov = NullProvider(tmp_path)
    assert isinstance(prov, Provider)


def test_null_provider_records_outbox(tmp_path):
    prov = NullProvider(tmp_path)
    ref = prov.create_issue("title", "body", labels=["epic"])
    assert ref.external_id.startswith("NULL-ISSUE")
    outbox = tmp_path / ".devagent" / "integrations" / "outbox.jsonl"
    lines = outbox.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["action"] == "issue"


def test_registry_defaults_to_null(tmp_path):
    class Cfg:
        raw = {}
    prov = registry.get_provider(Cfg(), tmp_path)
    assert prov.name == "null"


def test_registry_unknown_provider_degrades_to_null(tmp_path):
    class Cfg:
        raw = {"integrations": {"provider": "jira"}}  # no creds → IntegrationError → null
    prov = registry.get_provider(Cfg(), tmp_path)
    assert prov.name == "null"


def test_sync_epic_creates_issues_and_is_idempotent(tmp_path):
    epic = epic_mod.build_epic("E-0001", "feature", PLAN)
    prov = NullProvider(tmp_path)
    mapping = sync.sync_epic(tmp_path, epic, prov)
    # epic + 2 stories = 3 issues
    assert len(mapping) == 3
    assert "E-0001" in mapping and "E-0001.S1" in mapping

    # second sync creates nothing new
    before = prov._n
    sync.sync_epic(tmp_path, epic, prov)
    assert prov._n == before
