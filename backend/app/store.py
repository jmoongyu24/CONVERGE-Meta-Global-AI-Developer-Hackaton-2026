"""SQLite persistence: one file, data/converge.db by default, holds every workspace."""

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app import REPO_ROOT
from app.models import (
    Asset,
    BoardState,
    ConvergeSession,
    Member,
    Source,
    SourceAnalysis,
    Workspace,
)

# CONVERGE_DB points a server at another database, e.g. a scratch copy for tests or demos.
DB_PATH = Path(os.environ.get("CONVERGE_DB") or REPO_ROOT / "data" / "converge.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    notion_page_id TEXT
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
    basis        TEXT,  -- hash of the source text the model read; NULL for hand labels
    PRIMARY KEY (workspace_id, source_id, origin),
    FOREIGN KEY (workspace_id, source_id) REFERENCES sources (workspace_id, id) ON DELETE CASCADE
);
-- Images the sources embed; a source refers to one as `assets/<name>`.
CREATE TABLE IF NOT EXISTS assets (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    media_type   TEXT NOT NULL,
    data         BLOB NOT NULL,
    PRIMARY KEY (workspace_id, name)
);
-- The latest briefing per workspace and origin (BoardState JSON).
CREATE TABLE IF NOT EXISTS boards (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    origin       TEXT NOT NULL,
    board        TEXT NOT NULL,
    PRIMARY KEY (workspace_id, origin)
);
-- Converge Room sessions (ConvergeSession JSON), kept after the room closes.
CREATE TABLE IF NOT EXISTS converge_sessions (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    id           TEXT NOT NULL,
    session      TEXT NOT NULL,
    PRIMARY KEY (workspace_id, id)
);
-- Every model response, keyed by a hash of the request: a repeated call costs nothing, and
-- the rows double as the cost log.
CREATE TABLE IF NOT EXISTS llm_calls (
    key        TEXT PRIMARY KEY,
    model      TEXT NOT NULL,
    purpose    TEXT NOT NULL,
    response   TEXT NOT NULL,
    tokens_in  INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    cost       REAL NOT NULL,
    seconds    REAL NOT NULL,
    created_at TEXT NOT NULL
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
    # Databases created by earlier versions lack these columns.
    for table, column in [("workspaces", "notion_page_id"), ("analyses", "basis")]:
        if column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# --- Workspaces ----------------------------------------------------------------


def save_workspace(conn: sqlite3.Connection, workspace: Workspace) -> None:
    """Insert or update a workspace and replace its member list."""
    # An upsert, not INSERT OR REPLACE: a replace deletes the row first, and the
    # cascade would wipe the workspace's sources with it. Reloading a team file without a
    # Notion page keeps the link made earlier.
    conn.execute(
        """INSERT INTO workspaces (id, name, description, notion_page_id) VALUES (?, ?, ?, ?)
           ON CONFLICT (id) DO UPDATE SET name = excluded.name,
               description = excluded.description,
               notion_page_id = coalesce(excluded.notion_page_id, workspaces.notion_page_id)""",
        (workspace.id, workspace.name, workspace.description, workspace.notion_page_id),
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
        notion_page_id=row["notion_page_id"],
    )


def list_workspaces(conn: sqlite3.Connection) -> list[Workspace]:
    rows = conn.execute("SELECT * FROM workspaces ORDER BY name").fetchall()
    return [_workspace(conn, row) for row in rows]


def get_workspace(conn: sqlite3.Connection, workspace_id: str) -> Workspace | None:
    row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    return _workspace(conn, row) if row else None


def link_notion(conn: sqlite3.Connection, workspace_id: str, page_id: str | None) -> bool:
    """Write the workspace's future edits to this Notion page (None unlinks it)."""
    cursor = conn.execute(
        "UPDATE workspaces SET notion_page_id = ? WHERE id = ?", (page_id, workspace_id)
    )
    return cursor.rowcount == 1


def revision(conn: sqlite3.Connection, workspace_id: str) -> str:
    """A token that changes whenever the workspace's sources or analyses change, so open
    browsers can tell that a teammate edited something."""
    row = conn.execute(
        """SELECT (SELECT count(*) || '.' || coalesce(sum(length(text)), 0)
                   FROM sources WHERE workspace_id = :id)
               || '/' ||
               (SELECT count(*) || '.' || coalesce(sum(length(analysis)), 0)
                   FROM analyses WHERE workspace_id = :id)""",
        {"id": workspace_id},
    ).fetchone()
    return row[0]


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
    conn: sqlite3.Connection,
    workspace_id: str,
    origin: str,
    analyses: list[SourceAnalysis],
    bases: dict[str, str] | None = None,
) -> None:
    """Store one analysis per source; analysing a source again replaces it. `bases` records
    which version of each source text the analysis read."""
    conn.executemany(
        """INSERT INTO analyses (workspace_id, source_id, origin, analysis, basis)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (workspace_id, source_id, origin)
           DO UPDATE SET analysis = excluded.analysis, basis = excluded.basis""",
        [
            (workspace_id, a.source, origin, a.model_dump_json(), (bases or {}).get(a.source))
            for a in analyses
        ],
    )


def list_bases(conn: sqlite3.Connection, workspace_id: str, origin: str) -> dict[str, str | None]:
    rows = conn.execute(
        "SELECT source_id, basis FROM analyses WHERE workspace_id = ? AND origin = ?",
        (workspace_id, origin),
    ).fetchall()
    return {row["source_id"]: row["basis"] for row in rows}


def list_analyses(
    conn: sqlite3.Connection, workspace_id: str, origin: str
) -> dict[str, SourceAnalysis]:
    """Analyses of one origin, keyed by source id."""
    rows = conn.execute(
        "SELECT source_id, analysis FROM analyses WHERE workspace_id = ? AND origin = ?",
        (workspace_id, origin),
    ).fetchall()
    return {row["source_id"]: SourceAnalysis.model_validate_json(row["analysis"]) for row in rows}


# --- Assets --------------------------------------------------------------------


def save_assets(conn: sqlite3.Connection, workspace_id: str, assets: list[Asset]) -> None:
    conn.executemany(
        """INSERT INTO assets (workspace_id, name, media_type, data) VALUES (?, ?, ?, ?)
           ON CONFLICT (workspace_id, name)
           DO UPDATE SET media_type = excluded.media_type, data = excluded.data""",
        [(workspace_id, a.name, a.media_type, a.data) for a in assets],
    )


def get_asset(conn: sqlite3.Connection, workspace_id: str, name: str) -> Asset | None:
    row = conn.execute(
        "SELECT * FROM assets WHERE workspace_id = ? AND name = ?", (workspace_id, name)
    ).fetchone()
    return Asset(name=row["name"], media_type=row["media_type"], data=row["data"]) if row else None


# --- Briefings and model calls -------------------------------------------------


def save_board(conn: sqlite3.Connection, board: BoardState) -> None:
    conn.execute(
        """INSERT INTO boards (workspace_id, origin, board) VALUES (?, ?, ?)
           ON CONFLICT (workspace_id, origin) DO UPDATE SET board = excluded.board""",
        (board.workspace, board.origin, board.model_dump_json()),
    )


def get_board(conn: sqlite3.Connection, workspace_id: str, origin: str) -> BoardState | None:
    row = conn.execute(
        "SELECT board FROM boards WHERE workspace_id = ? AND origin = ?", (workspace_id, origin)
    ).fetchone()
    return BoardState.model_validate_json(row["board"]) if row else None


def get_llm_call(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT response FROM llm_calls WHERE key = ?", (key,)).fetchone()
    return row["response"] if row else None


def save_llm_call(conn: sqlite3.Connection, key: str, **call: str | int | float) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO llm_calls
           (key, model, purpose, response, tokens_in, tokens_out, cost, seconds, created_at)
           VALUES (:key, :model, :purpose, :response, :tokens_in, :tokens_out, :cost, :seconds,
                   :created_at)""",
        {"key": key, **call},
    )


def spent(conn: sqlite3.Connection) -> float:
    """Dollars spent on model calls so far."""
    return conn.execute("SELECT coalesce(sum(cost), 0) FROM llm_calls").fetchone()[0]


def save_converge(conn: sqlite3.Connection, session: ConvergeSession) -> None:
    conn.execute(
        """INSERT INTO converge_sessions (workspace_id, id, session) VALUES (?, ?, ?)
           ON CONFLICT (workspace_id, id) DO UPDATE SET session = excluded.session""",
        (session.workspace, session.id, session.model_dump_json()),
    )


def get_converge(
    conn: sqlite3.Connection, workspace_id: str, session_id: str
) -> ConvergeSession | None:
    row = conn.execute(
        "SELECT session FROM converge_sessions WHERE workspace_id = ? AND id = ?",
        (workspace_id, session_id),
    ).fetchone()
    return ConvergeSession.model_validate_json(row["session"]) if row else None
