"""FastAPI app entrypoint.

Mounts the API router and serves the static frontend. The static mount is
added LAST: `StaticFiles(html=True)` mounted at "/" would otherwise match
every request path (including /v1/*) before the router gets a chance to
handle it.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routes import router

app = FastAPI(title="UML Generation Platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if not STATIC_DIR.exists():
    STATIC_DIR.mkdir(parents=True)
    (STATIC_DIR / ".gitkeep").touch()

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
