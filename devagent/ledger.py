"""SQLite task ledger. Records cost (actual + counterfactual), the quality-gate result, and
whether the task stayed in the parity envelope — the raw material for `cost` and `quality`."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT,
    session_id          TEXT,
    task                TEXT,
    files               TEXT,
    models_used         TEXT,
    tokens_in           INTEGER,
    tokens_out          INTEGER,
    actual_cost         REAL,
    counterfactual_cost REAL,
    savings             REAL,
    quality_gate        TEXT,
    in_envelope         INTEGER,
    decomposed          INTEGER,
    n_subtasks          INTEGER,
    audit_result        TEXT,
    status              TEXT
);

CREATE TABLE IF NOT EXISTS audits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT,
    run_kind        TEXT,      -- 'audit' | 'calibrate'
    run_id          TEXT,
    task            TEXT,
    repo            TEXT,
    context_tokens  INTEGER,
    max_file_lines  INTEGER,
    verdict         TEXT,      -- local_better | equivalent | frontier_better | skipped
    local_model     TEXT,
    frontier_model  TEXT,
    judge_model     TEXT,
    reason          TEXT
);
"""

PARITY_VERDICTS = ("local_better", "equivalent")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _connect(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)


def log_task(db_path: Path, record: dict) -> int:
    init_db(db_path)
    record.setdefault("created_at", _now())
    cols = [
        "created_at", "session_id", "task", "files", "models_used", "tokens_in",
        "tokens_out", "actual_cost", "counterfactual_cost", "savings", "quality_gate",
        "in_envelope", "decomposed", "n_subtasks", "audit_result", "status",
    ]
    for jsonish in ("files", "models_used", "quality_gate", "audit_result"):
        if isinstance(record.get(jsonish), (list, dict)):
            record[jsonish] = json.dumps(record[jsonish])
    with _connect(db_path) as conn:
        cur = conn.execute(
            f"INSERT INTO tasks ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [record.get(c) for c in cols],
        )
        return int(cur.lastrowid)


def recent(db_path: Path, limit: int = 20) -> list[sqlite3.Row]:
    init_db(db_path)
    with _connect(db_path) as conn:
        return list(conn.execute(
            "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)))


def log_audit(db_path: Path, record: dict) -> int:
    init_db(db_path)
    record.setdefault("created_at", _now())
    cols = ["created_at", "run_kind", "run_id", "task", "repo", "context_tokens",
            "max_file_lines", "verdict", "local_model", "frontier_model", "judge_model", "reason"]
    with _connect(db_path) as conn:
        cur = conn.execute(
            f"INSERT INTO audits ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [record.get(c) for c in cols],
        )
        return int(cur.lastrowid)


def audits_summary(db_path: Path, run_id: str | None = None) -> dict:
    init_db(db_path)
    where = "WHERE verdict != 'skipped'"
    params: tuple = ()
    if run_id:
        where += " AND run_id = ?"
        params = (run_id,)
    placeholders = ",".join("?" for _ in PARITY_VERDICTS)
    with _connect(db_path) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM audits {where}", params).fetchone()[0]
        parity = conn.execute(
            f"SELECT COUNT(*) FROM audits {where} AND verdict IN ({placeholders})",
            (*params, *PARITY_VERDICTS),
        ).fetchone()[0]
    return {"scored": total, "parity": parity}


def audits_by_bucket(db_path: Path, run_id: str, buckets: list[tuple[int, int]]) -> list[dict]:
    """Parity rate per context-token bucket, for a calibration run."""
    init_db(db_path)
    out = []
    placeholders = ",".join("?" for _ in PARITY_VERDICTS)
    with _connect(db_path) as conn:
        for lo, hi in buckets:
            total = conn.execute(
                "SELECT COUNT(*) FROM audits WHERE run_id=? AND verdict!='skipped' "
                "AND context_tokens>=? AND context_tokens<?", (run_id, lo, hi)).fetchone()[0]
            parity = conn.execute(
                f"SELECT COUNT(*) FROM audits WHERE run_id=? AND verdict IN ({placeholders}) "
                "AND context_tokens>=? AND context_tokens<?",
                (run_id, *PARITY_VERDICTS, lo, hi)).fetchone()[0]
            out.append({"lo": lo, "hi": hi, "total": total, "parity": parity})
    return out


def totals(db_path: Path) -> dict:
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute("""
            SELECT
                COUNT(*)                          AS n,
                COALESCE(SUM(actual_cost), 0)         AS actual,
                COALESCE(SUM(counterfactual_cost), 0) AS counterfactual,
                COALESCE(SUM(savings), 0)             AS savings,
                COALESCE(SUM(in_envelope), 0)         AS in_envelope,
                COALESCE(SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END), 0) AS applied
            FROM tasks
        """).fetchone()
    return dict(row) if row else {}
