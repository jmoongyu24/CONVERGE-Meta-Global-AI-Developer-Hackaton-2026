"""Converge API server.

Serves the REST API under /api and, once `npm run build` has produced frontend/dist,
the web UI from the same process (one server to deploy).
"""

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from app import REPO_ROOT, pipeline, store
from app.models import LineageState, Source, Workspace

FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

app = FastAPI(title="Converge API", version="0.1.0")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": app.version}


@app.get("/api/workspaces")
def list_workspaces() -> list[Workspace]:
    with store.connect() as conn:
        return store.list_workspaces(conn)


@app.get("/api/workspaces/{workspace_id}/state")
def lineage_state(
    workspace_id: str, as_of: str | None = None, origin: str = "gold"
) -> LineageState:
    """The team's idea lineage after source `as_of` (default: the latest source).

    `origin` picks whose analyses to replay: "gold" (hand labels) or "llm" (model output).
    """
    with store.connect() as conn:
        workspace = store.get_workspace(conn, workspace_id)
        sources = store.list_sources(conn, workspace_id)
        analyses = store.list_analyses(conn, workspace_id, origin)
    if workspace is None or not sources:
        raise HTTPException(404, f"workspace {workspace_id!r} not found or has no sources")
    if as_of is not None and as_of not in {s.id for s in sources}:
        raise HTTPException(404, f"source {as_of!r} not found")
    return pipeline.build_state(workspace, sources, analyses, as_of)


@app.get("/api/workspaces/{workspace_id}/sources/{source_id}")
def get_source(workspace_id: str, source_id: str) -> Source:
    """One source with its full text, so the UI can show a quote in context."""
    with store.connect() as conn:
        source = store.get_source(conn, workspace_id, source_id)
    if source is None:
        raise HTTPException(404, f"source {source_id!r} not found")
    return source


# Mounted last: "/" matches every path, so all /api routes must be registered above.
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="web")
