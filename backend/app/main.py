"""Converge API server.

Serves the REST/SSE API under /api and, once `npm run build` has produced
frontend/dist, the web UI from the same process (one server to deploy).
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import REPO_ROOT

FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

app = FastAPI(title="Converge API", version="0.1.0")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": app.version}


# Mounted last: "/" matches every path, so all /api routes must be registered above.
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="web")
