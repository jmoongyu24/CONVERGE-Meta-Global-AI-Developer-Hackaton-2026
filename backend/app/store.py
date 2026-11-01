"""SQLite persistence: one file, data/converge.db, holds every workspace."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app import REPO_ROOT
from app.models import Member, Source, Workspace

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
"""


@contextmanager
def connect(path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    """Open the database, creating tables if needed; commit when the block succeeds."""
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


def list_workspaces(conn: sqlite3.Connection) -> list[Workspace]:
    workspaces = []
    for row in conn.execute("SELECT * FROM workspaces ORDER BY name"):
        members = conn.execute(
            "SELECT * FROM members WHERE workspace_id = ? ORDER BY rowid", (row["id"],)
        )
        workspaces.append(
            Workspace(
                id=row["id"],
                name=row["name"],
                description=row["description"],
                members=[
                    Member(
                        id=m["id"], name=m["name"], role=m["role"], aliases=json.loads(m["aliases"])
                    )
                    for m in members
                ],
            )
        )
    return workspaces


def list_sources(conn: sqlite3.Connection, workspace_id: str) -> list[Source]:
    """A workspace's sources in time order."""
    rows = conn.execute(
        "SELECT * FROM sources WHERE workspace_id = ? ORDER BY occurred_at", (workspace_id,)
    )
    return [
        Source(
            id=r["id"],
            kind=r["kind"],
            occurred_at=datetime.fromisoformat(r["occurred_at"]),
            title=r["title"],
            text=r["text"],
            url=r["url"],
        )
        for r in rows
    ]
