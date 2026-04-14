from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from routes import ingest, investigate, pr, webhooks
from services.neo4j_service import neo4j_service
from services.qdrant_service import qdrant_service
from jobs.azure_monitor_poller import run_azure_monitor_poll_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = asyncio.Event()
    poll_task: asyncio.Task | None = None
    try:
        await qdrant_service.ensure_collection()
    except Exception as exc:
        print(f"[kairos] Qdrant init warning: {exc}")
    try:
        neo4j_service.ensure_schema()
    except Exception as exc:
        print(f"[kairos] Neo4j init warning: {exc}")

    if settings.azure_monitor_enabled:
        poll_task = asyncio.create_task(
            run_azure_monitor_poll_loop(stop_event, interval_minutes=settings.azure_poll_interval_minutes)
        )
    yield
    stop_event.set()
    if poll_task is not None:
        try:
            await poll_task
        except Exception:
            pass
    try:
        neo4j_service.close()
    except Exception:
        pass


app = FastAPI(title="KAIROS", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(investigate.router, prefix="/api", tags=["investigate"])
app.include_router(ingest.router, prefix="/api", tags=["ingest"])
app.include_router(webhooks.router, prefix="/api", tags=["webhooks"])
app.include_router(pr.router, prefix="/api", tags=["pr"])


@app.get("/health")
async def health():
    return {"status": "ok", "confidence_threshold": settings.confidence_threshold}


@app.get("/")
async def root():
    return {"service": "KAIROS", "docs": "/docs"}
