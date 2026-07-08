"""
FastAPI application entry-point for the K3s Pod Failure & RCA Dashboard.

Startup sequence
----------------
1. Create all SQLite tables (idempotent).
2. Start background services: K8s/Docker/Prometheus/Longhorn collectors,
   RCA engine, APScheduler (retention).
3. Mount all API routers and the WebSocket endpoint.
4. Serve the static frontend and the Jinja2 index template.

Shutdown sequence
-----------------
1. Cancel all background asyncio tasks cleanly.
2. APScheduler stops without waiting for running jobs.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.core.database import Base, engine
from app.background import services

# Routers
from app.routers.cluster import router as cluster_router
from app.routers.nodes import router as nodes_router
from app.routers.pods import router as pods_router
from app.routers.longhorn import router as longhorn_router
from app.routers.events import router as events_router
from app.routers.rca import router as rca_router
from app.routers.alerts import router as alerts_router
from app.routers.rankings import router as rankings_router
from app.routers.ws import router as ws_router

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("rca.main")

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- Startup ----
    logger.info("Creating database tables …")
    Base.metadata.create_all(bind=engine)
    logger.info("Starting background services …")
    await services.start()
    logger.info("%s is ready", settings.APP_NAME)

    yield  # ← application runs here

    # ---- Shutdown ----
    logger.info("Stopping background services …")
    await services.stop()
    logger.info("Shutdown complete")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description=(
        "Kubernetes Pod Failure Detection & Root Cause Analysis Dashboard "
        "for K3s clusters.  Real-time metrics from Kubernetes, Prometheus, "
        "Docker, and Longhorn; rule-based RCA engine; WebSocket live updates."
    ),
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — allow the React dev server (port 3000) during development
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# API Routers
# ---------------------------------------------------------------------------
app.include_router(cluster_router)
app.include_router(nodes_router)
app.include_router(pods_router)
app.include_router(longhorn_router)
app.include_router(events_router)
app.include_router(rca_router)
app.include_router(alerts_router)
app.include_router(rankings_router)
app.include_router(ws_router)

# ---------------------------------------------------------------------------
# Static files + Jinja2 templates
# ---------------------------------------------------------------------------
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "app", "static")
_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "app", "templates")

if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

templates = Jinja2Templates(directory=_TEMPLATES_DIR)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_index(request: Request):
    """Serve the dashboard SPA shell."""
    return templates.TemplateResponse("index.html", {"request": request})


# ---------------------------------------------------------------------------
# Health-check endpoint (used by Kubernetes liveness probe)
# ---------------------------------------------------------------------------

@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Dev entry-point: python main.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.ENV != "production",
        log_level="info",
    )
