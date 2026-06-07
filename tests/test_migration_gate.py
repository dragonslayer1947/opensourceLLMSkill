from dataclasses import dataclass

from devagent.validate import migration_gate


@dataclass
class Change:
    path: str
    new: str = ""


def test_is_migration_path():
    assert migration_gate.is_migration_path("db/migrations/0007_x.py")
    assert migration_gate.is_migration_path("alembic/versions/abc.py")
    assert not migration_gate.is_migration_path("svc/models.py")


def test_destructive_requires_flag():
    ch = Change("migrations/0008.py", "def upgrade():\n    op.execute('DROP TABLE orders')\ndef downgrade():\n    pass\n")
    v = migration_gate.check([ch], flags=set())
    assert any(x.rule_id == "migration-destructive" and x.severity == "block" for x in v)
    v2 = migration_gate.check([ch], flags={"migration-approved"})
    assert not any(x.rule_id == "migration-destructive" for x in v2)


def test_irreversible_blocked():
    ch = Change("migrations/0009.py", "def upgrade():\n    op.add_column('x')\n")
    v = migration_gate.check([ch], flags=set())
    assert any(x.rule_id == "migration-irreversible" for x in v)


def test_reversible_nondestructive_ok():
    ch = Change("migrations/0010.py",
                "def upgrade():\n    op.add_column('x')\ndef downgrade():\n    op.drop_column('x')\n")
    # has downgrade; DROP COLUMN present but only inside downgrade — still flagged destructive
    # unless approved. The intent: destructive ops anywhere need the flag.
    v = migration_gate.check([ch], flags={"migration-approved"})
    assert v == []


def test_non_migration_file_ignored():
    ch = Change("svc/models.py", "DROP TABLE everything")
    assert migration_gate.check([ch], flags=set()) == []
