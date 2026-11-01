"""Converge API server.

Serves the REST API under /api and, once `npm run build` has produced frontend/dist,
the web UI from the same process (one server to deploy).
"""

from datetime import datetime, timedelta

import httpx
from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from notion_client.errors import NotionClientErrorBase

from app import REPO_ROOT, ingest, pipeline, store
from app.models import (
    EditRequest,
    EditResult,
    LineageState,
    Source,
    SourceAnalysis,
    Workspace,
)

FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
NOTION_ERRORS = (RuntimeError, NotionClientErrorBase, httpx.HTTPError)

app = FastAPI(title="Converge API", version="0.1.0")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": app.version}


@app.get("/api/workspaces")
def list_workspaces() -> list[Workspace]:
    with store.connect() as conn:
        return store.list_workspaces(conn)


def load_state(workspace_id: str, as_of: str | None = None, origin: str = "gold") -> LineageState:
    with store.connect() as conn:
        workspace = store.get_workspace(conn, workspace_id)
        sources = store.list_sources(conn, workspace_id)
        # Edits made in the UI apply whichever analyses are replayed.
        analyses = {
            **store.list_analyses(conn, workspace_id, origin),
            **store.list_analyses(conn, workspace_id, "user"),
        }
    if workspace is None or not sources:
        raise HTTPException(404, f"workspace {workspace_id!r} not found or has no sources")
    if as_of is not None and as_of not in {s.id for s in sources}:
        raise HTTPException(404, f"source {as_of!r} not found")
    return pipeline.build_state(workspace, sources, analyses, as_of)


@app.get("/api/workspaces/{workspace_id}/state")
def lineage_state(
    workspace_id: str, as_of: str | None = None, origin: str = "gold"
) -> LineageState:
    """The team's idea lineage and architecture after source `as_of` (default: the latest).

    `origin` picks whose analyses to replay: "gold" (hand labels) or "llm" (model output).
    """
    return load_state(workspace_id, as_of, origin)


@app.get("/api/workspaces/{workspace_id}/revision")
def revision(workspace_id: str) -> dict[str, str]:
    """Cheap to poll: changes whenever anyone adds or edits a source of the workspace."""
    with store.connect() as conn:
        return {"revision": store.revision(conn, workspace_id)}


@app.get("/api/workspaces/{workspace_id}/sources/{source_id}")
def get_source(workspace_id: str, source_id: str) -> Source:
    """One source with its full text, so the UI can show a quote in context."""
    with store.connect() as conn:
        source = store.get_source(conn, workspace_id, source_id)
    if source is None:
        raise HTTPException(404, f"source {source_id!r} not found")
    return source


@app.get("/api/workspaces/{workspace_id}/assets/{name}")
def get_asset(workspace_id: str, name: str) -> Response:
    """An image a source embeds as `assets/<name>`."""
    with store.connect() as conn:
        asset = store.get_asset(conn, workspace_id, name)
    if asset is None:
        raise HTTPException(404, f"asset {name!r} not found")
    return Response(asset.data, media_type=asset.media_type)


@app.post("/api/workspaces/{workspace_id}/edits")
def make_edit(workspace_id: str, edit: EditRequest) -> EditResult:
    """Record a member's change to the architecture as a line of today's session note.

    The session note is a source like any meeting, so the change shows up on the timeline,
    keeps its quote, and reaches teammates on their next poll. When the workspace is linked
    to a Notion page, the line is written there too.
    """
    state = load_state(workspace_id)
    last = state.sources[-1]
    now = max(datetime.now(), last.occurred_at + timedelta(minutes=1)).replace(microsecond=0)
    try:
        line, change = pipeline.compose_edit(state, edit, f"{now:%H:%M}")
    except ValueError as error:
        raise HTTPException(400, str(error)) from error

    session_id = f"session-{now:%Y-%m-%d}"
    with store.connect() as conn:
        session = store.get_source(conn, workspace_id, session_id) or Source(
            id=session_id,
            kind="session",
            occurred_at=now,
            title=f"{ingest.SESSION_TITLE} — {now:%b} {now.day}, {now.year}",
            text=(
                f"# {ingest.SESSION_TITLE} — {now:%b} {now.day}, {now.year}\n\n"
                "Changes the team made in Converge, one per line, in each member's words.\n"
            ),
        )
        blank = "" if "\n- " in session.text else "\n"  # one blank line under the header
        session.text = f"{session.text.rstrip()}\n{blank}- {line}\n"
        analysis = store.list_analyses(conn, workspace_id, "user").get(
            session_id
        ) or SourceAnalysis(source=session_id)
        analysis.modules += change.modules
        analysis.mentions += change.mentions
        analysis.items += change.items

        notion = "off"
        if state.workspace.notion_page_id:
            try:
                session.url = ingest.write_session_line(
                    state.workspace.notion_page_id, session, line
                )
                notion = "synced"
            except NOTION_ERRORS:
                notion = "failed"
        store.save_sources(conn, workspace_id, [session])
        store.save_analyses(conn, workspace_id, "user", [analysis])
    changed = [*change.modules, *change.items]
    module = (changed[0].module if changed else None) or edit.module or ""
    return EditResult(source=session_id, line=line, module=module, notion=notion)


# Mounted last: "/" matches every path, so all /api routes must be registered above.
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="web")
