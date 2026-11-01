"""SQLite persistence: one file, data/converge.db, holds every workspace."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app import REPO_ROOT
from app.models import Member, Source, SourceAnalysis, Workspace

DB_PATH = REPO_ROOT / "data" / "converge.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS members (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    id           TEXT NOT NULL,
    name         TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT '',
    aliases      TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (workspace_id, id)
);
CREATE TABLE IF NOT EXISTS sources (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    id           TEXT NOT NULL,
    kind         TEXT NOT NULL,
    occurred_at  TEXT NOT NULL,
    title        TEXT NOT NULL,
    text         TEXT NOT NULL,
    url          TEXT,
    PRIMARY KEY (workspace_id, id)
);
-- One SourceAnalysis (JSON) per source and origin: 'gold' = hand labels, 'llm' = model output.
-- Ideas, edges and stances are not stored; the pipeline rebuilds them by replaying these.
CREATE TABLE IF NOT EXISTS analyses (
    workspace_id TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    origin       TEXT NOT NULL,
    analysis     TEXT NOT NULL,
    PRIMARY KEY (workspace_id, source_id, origin),
    FOREIGN KEY (workspace_id, source_id) REFERENCES sources (workspace_id, id) ON DELETE CASCADE
);
"""


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open the database (DB_PATH by default), creating tables if needed; commit on success."""
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# --- Workspaces ----------------------------------------------------------------


def save_workspace(conn: sqlite3.Connection, workspace: Workspace) -> None:
    """Insert or update a workspace and replace its member list."""
    # An upsert, not INSERT OR REPLACE: a replace deletes the row first, and the
    # cascade would wipe the workspace's sources with it.
    conn.execute(
        """INSERT INTO workspaces (id, name, description) VALUES (?, ?, ?)
           ON CONFLICT (id) DO UPDATE SET name = excluded.name,
               description = excluded.description""",
        (workspace.id, workspace.name, workspace.description),
    )
    conn.execute("DELETE FROM members WHERE workspace_id = ?", (workspace.id,))
    conn.executemany(
        "INSERT INTO members (workspace_id, id, name, role, aliases) VALUES (?, ?, ?, ?, ?)",
        [(workspace.id, m.id, m.name, m.role, json.dumps(m.aliases)) for m in workspace.members],
    )


def _workspace(conn: sqlite3.Connection, row: sqlite3.Row) -> Workspace:
    members = conn.execute(
        "SELECT * FROM members WHERE workspace_id = ? ORDER BY rowid", (row["id"],)
    ).fetchall()
    return Workspace(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        members=[
            Member(id=m["id"], name=m["name"], role=m["role"], aliases=json.loads(m["aliases"]))
            for m in members
        ],
    )


def list_workspaces(conn: sqlite3.Connection) -> list[Workspace]:
    rows = conn.execute("SELECT * FROM workspaces ORDER BY name").fetchall()
    return [_workspace(conn, row) for row in rows]


def get_workspace(conn: sqlite3.Connection, workspace_id: str) -> Workspace | None:
    row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    return _workspace(conn, row) if row else None


# --- Sources -------------------------------------------------------------------


def save_sources(conn: sqlite3.Connection, workspace_id: str, sources: list[Source]) -> None:
    """Insert new sources; a source imported again (same id) is updated in place."""
    conn.executemany(
        """INSERT INTO sources (workspace_id, id, kind, occurred_at, title, text, url)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (workspace_id, id) DO UPDATE SET kind = excluded.kind,
               occurred_at = excluded.occurred_at, title = excluded.title,
               text = excluded.text, url = excluded.url""",
        [
            (workspace_id, s.id, s.kind, s.occurred_at.isoformat(), s.title, s.text, s.url)
            for s in sources
        ],
    )


def _source(row: sqlite3.Row) -> Source:
    return Source(
        id=row["id"],
        kind=row["kind"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        title=row["title"],
        text=row["text"],
        url=row["url"],
    )


def list_sources(conn: sqlite3.Connection, workspace_id: str) -> list[Source]:
    """A workspace's sources in time order."""
    rows = conn.execute(
        "SELECT * FROM sources WHERE workspace_id = ? ORDER BY occurred_at", (workspace_id,)
    ).fetchall()
    return [_source(row) for row in rows]


def get_source(conn: sqlite3.Connection, workspace_id: str, source_id: str) -> Source | None:
    row = conn.execute(
        "SELECT * FROM sources WHERE workspace_id = ? AND id = ?", (workspace_id, source_id)
    ).fetchone()
    return _source(row) if row else None


# --- Analyses ------------------------------------------------------------------


def save_analyses(
    conn: sqlite3.Connection, workspace_id: str, origin: str, analyses: list[SourceAnalysis]
) -> None:
    """Store one analysis per source; analysing a source again replaces it."""
    conn.executemany(
        """INSERT INTO analyses (workspace_id, source_id, origin, analysis) VALUES (?, ?, ?, ?)
           ON CONFLICT (workspace_id, source_id, origin)
           DO UPDATE SET analysis = excluded.analysis""",
        [(workspace_id, a.source, origin, a.model_dump_json()) for a in analyses],
    )


def list_analyses(
    conn: sqlite3.Connection, workspace_id: str, origin: str
) -> dict[str, SourceAnalysis]:
    """Analyses of one origin, keyed by source id."""
    rows = conn.execute(
        "SELECT source_id, analysis FROM analyses WHERE workspace_id = ? AND origin = ?",
        (workspace_id, origin),
    ).fetchall()
    return {row["source_id"]: SourceAnalysis.model_validate_json(row["analysis"]) for row in rows}
