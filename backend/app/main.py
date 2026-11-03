"""Converge API server.

Serves the REST API under /api and, once `npm run build` has produced frontend/dist,
the web UI from the same process (one server to deploy).
"""

import sqlite3
import threading
from datetime import datetime, timedelta

import httpx
from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from notion_client.errors import NotionClientErrorBase
from pydantic import BaseModel

from app import REPO_ROOT, converge, ingest, pipeline, store
from app.models import (
    AdoptRequest,
    BoardState,
    ConvergeRequest,
    ConvergeSession,
    EditRequest,
    EditResult,
    LineageState,
    Source,
    SourceAnalysis,
    UpdateStatus,
    Workspace,
)

FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
NOTION_ERRORS = (RuntimeError, NotionClientErrorBase, httpx.HTTPError)

app = FastAPI(title="Converge API", version="0.2.0")
# Update jobs run in a background thread per workspace; one server process holds them all.
jobs: dict[str, UpdateStatus] = {}
jobs_lock = threading.Lock()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": app.version}


@app.get("/api/workspaces")
def list_workspaces() -> list[Workspace]:
    with store.connect() as conn:
        return store.list_workspaces(conn)


def pick_origin(conn: sqlite3.Connection, workspace_id: str, origin: str) -> str:
    """ "auto" means the model's analyses once the workspace has been updated, else gold."""
    if origin != "auto":
        return origin
    return "llm" if store.list_bases(conn, workspace_id, "llm") else "gold"


def load_state(workspace_id: str, as_of: str | None = None, origin: str = "auto") -> LineageState:
    with store.connect() as conn:
        workspace = store.get_workspace(conn, workspace_id)
        sources = store.list_sources(conn, workspace_id)
        origin = pick_origin(conn, workspace_id, origin)
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
    workspace_id: str, as_of: str | None = None, origin: str = "auto"
) -> LineageState:
    """The team's idea lineage and architecture after source `as_of` (default: the latest).

    `origin` picks whose analyses to replay: "gold" (hand labels), "llm" (model output), or
    "auto" (the model's once the workspace has been updated).
    """
    return load_state(workspace_id, as_of, origin)


@app.get("/api/workspaces/{workspace_id}/board")
def board(workspace_id: str, origin: str = "auto") -> BoardState:
    """The briefing written at the last update."""
    with store.connect() as conn:
        found = store.get_board(conn, workspace_id, pick_origin(conn, workspace_id, origin))
    if found is None:
        raise HTTPException(404, "no briefing yet: press Update")
    return found


def run_update(workspace_id: str) -> None:
    def report(**changes: object) -> None:
        with jobs_lock:
            jobs[workspace_id] = jobs[workspace_id].model_copy(update=changes)

    try:
        pipeline.update_workspace(workspace_id, report)
        report(state="done", step="Up to date")
    except Exception as error:  # any failure must reach the person waiting on the button
        report(state="failed", error=str(error) or type(error).__name__)


@app.post("/api/workspaces/{workspace_id}/update")
def start_update(workspace_id: str) -> UpdateStatus:
    """Read the linked Notion page and every new note with the model, then rewrite the
    briefing. Runs in the background; poll GET for progress."""
    with jobs_lock:
        if workspace_id in jobs and jobs[workspace_id].state == "running":
            return jobs[workspace_id]
        jobs[workspace_id] = UpdateStatus(state="running", step="Starting")
    threading.Thread(target=run_update, args=(workspace_id,), daemon=True).start()
    return jobs[workspace_id]


@app.get("/api/workspaces/{workspace_id}/update")
def update_status(workspace_id: str) -> UpdateStatus:
    with jobs_lock:
        return jobs.get(workspace_id, UpdateStatus(state="idle"))


class ConnectRequest(BaseModel):
    url: str  # a Notion page link, or its id


@app.post("/api/workspaces/connect")
def connect_notion(request: ConnectRequest) -> Workspace:
    """Make a workspace for a Notion page (the page must be shared with the integration).
    Its members are found in the notes on the first update."""
    try:
        page_id = ingest.notion_page_id(request.url.replace("-", ""))
    except IndexError as error:
        raise HTTPException(400, "that does not look like a Notion page link") from error
    with store.connect() as conn:
        for workspace in store.list_workspaces(conn):
            if (workspace.notion_page_id or "").replace("-", "") == page_id:
                return workspace
    try:
        client = ingest.notion()
        try:
            title = ingest.page_title(client.pages.retrieve(page_id=page_id))
        except NotionClientErrorBase:
            database = client.databases.retrieve(database_id=page_id)
            title = ingest.plain_text(database.get("title", [])) or "Notion database"
    except NOTION_ERRORS as error:
        raise HTTPException(
            400,
            "Converge cannot open that page. In Notion, open the page menu (•••) → "
            "Connections and add your integration, then try again.",
        ) from error
    workspace = Workspace(
        id=f"{pipeline.slug(title)[:40]}-{page_id[:6]}",
        name=title,
        description="Connected Notion page",
        members=[],
        notion_page_id=page_id,
    )
    with store.connect() as conn:
        store.save_workspace(conn, workspace)
    return workspace


@app.get("/api/notion/{page_id}")
def find_notion_page(page_id: str) -> dict[str, str | None]:
    """Which workspace a Notion page belongs to: the linked page, one of its notes, the
    session page Converge writes, or any page below the linked one. The browser extension
    asks this for the page open beside it, so the panel keeps the team on every sub-page."""
    wanted = page_id.replace("-", "")
    with store.connect() as conn:
        workspaces = store.list_workspaces(conn)
        linked = {(w.notion_page_id or "").replace("-", ""): w.id for w in workspaces}
        if wanted in linked:
            return {"workspace": linked[wanted]}
        for workspace in workspaces:
            for source in store.list_sources(conn, workspace.id):
                if wanted in (source.id.replace("-", ""), (source.url or "").replace("-", "")):
                    return {"workspace": workspace.id}
    linked.pop("", None)
    if linked:
        try:
            for ancestor in ingest.notion_ancestors(wanted):
                if ancestor in linked:
                    return {"workspace": linked[ancestor]}
        except NOTION_ERRORS:
            pass  # a page the integration cannot see belongs to no team here
    return {"workspace": None}


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


def append_session(
    state: LineageState, now: datetime, lines: list[str], change: SourceAnalysis
) -> tuple[str, str]:
    """Add lines and the analysis entries they stand for to today's session note, and mirror
    the lines into the linked Notion page. Returns the note's id and the Notion outcome."""
    workspace_id = state.workspace.id
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
        for line in lines:
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
                for line in lines:
                    session.url = ingest.write_session_line(
                        state.workspace.notion_page_id, session, line
                    )
                notion = "synced"
            except NOTION_ERRORS:
                notion = "failed"
        store.save_sources(conn, workspace_id, [session])
        store.save_analyses(conn, workspace_id, "user", [analysis])
    return session_id, notion


def session_clock(state: LineageState) -> datetime:
    """Now, but never before the latest source, so a session note sorts last."""
    last = state.sources[-1].occurred_at
    return max(datetime.now(), last + timedelta(minutes=1)).replace(microsecond=0)


@app.post("/api/workspaces/{workspace_id}/edits")
def make_edit(workspace_id: str, edit: EditRequest) -> EditResult:
    """Record a member's change to the architecture as a line of today's session note.

    The session note is a source like any meeting, so the change shows up on the timeline,
    keeps its quote, and reaches teammates on their next poll. When the workspace is linked
    to a Notion page, the line is written there too.
    """
    state = load_state(workspace_id)
    now = session_clock(state)
    try:
        line, change = pipeline.compose_edit(state, edit, f"{now:%H:%M}")
    except ValueError as error:
        raise HTTPException(400, str(error)) from error

    session_id, notion = append_session(state, now, [line], change)
    changed = [*change.modules, *change.items]
    module = (changed[0].module if changed else None) or edit.module or ""
    return EditResult(source=session_id, line=line, module=module, notion=notion)


# --- Converge Room ---------------------------------------------------------------


@app.post("/api/workspaces/{workspace_id}/converge")
def open_converge(workspace_id: str, request: ConvergeRequest) -> ConvergeSession:
    """Start a Converge Room on an open question; poll GET for the delegates' turns and the
    facilitator's proposals as they arrive."""
    load_state(workspace_id)  # 404 early for an unknown workspace

    def load() -> tuple[LineageState, list[Source]]:
        with store.connect() as conn:
            sources = store.list_sources(conn, workspace_id)
        return load_state(workspace_id), sources

    return converge.start(workspace_id, request, load)


@app.get("/api/workspaces/{workspace_id}/converge/{session_id}")
def converge_session(workspace_id: str, session_id: str) -> ConvergeSession:
    session = converge.get(workspace_id, session_id)
    if session is None:
        raise HTTPException(404, "no such Converge Room session")
    return session


@app.post("/api/workspaces/{workspace_id}/converge/{session_id}/adopt")
def adopt_proposal(workspace_id: str, session_id: str, request: AdoptRequest) -> EditResult:
    """Write the adopted proposal to the session note (and Notion), then rewrite the briefing
    in the background so the next steps follow the decision."""
    session = converge.get(workspace_id, session_id)
    if session is None or session.state != "done":
        raise HTTPException(404, "no finished Converge Room session with that id")
    state = load_state(workspace_id)
    now = session_clock(state)
    try:
        lines, change = converge.adoption(
            state, session, request.proposal, request.member, f"{now:%H:%M}"
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    source, notion = append_session(state, now, lines, change)
    adopted = session.model_copy(update={"adopted": request.proposal})
    with converge.sessions_lock:
        converge.sessions[session_id] = adopted
    with store.connect() as conn:
        store.save_converge(conn, adopted)
    start_update(workspace_id)
    return EditResult(source=source, line=lines[0], module=session.module or "", notion=notion)


# Mounted last: "/" matches every path, so all /api routes must be registered above.
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="web")
