from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from routes import ingest, investigate, pr, webhooks
from services.qdrant_service import qdrant_service
from jobs.azure_monitor_poller import run_azure_monitor_poll_loop
from jobs.teams_graph_poller import run_teams_graph_poll_loop
from routes.ingest import run_jira_backfill


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = asyncio.Event()
    poll_task: asyncio.Task | None = None
    teams_task: asyncio.Task | None = None
    jira_backfill_task: asyncio.Task | None = None
    try:
        await qdrant_service.ensure_collection()
    except Exception as exc:
        print(f"[kairos] Qdrant init warning: {exc}")

    if settings.azure_monitor_enabled:
        poll_task = asyncio.create_task(
            run_azure_monitor_poll_loop(stop_event, interval_minutes=settings.azure_poll_interval_minutes)
        )
    if settings.teams_sync_enabled:
        teams_task = asyncio.create_task(
            run_teams_graph_poll_loop(stop_event, interval_minutes=settings.teams_poll_interval_minutes)
        )

    if settings.jira_backfill_on_startup:
        async def _jira_startup_backfill():
            try:
                print("[kairos] Jira backfill on startup: starting")
                res = await run_jira_backfill(jql=settings.jira_backfill_jql, max_issues=settings.jira_backfill_max_issues)
                if res.get("error"):
                    print(f"[kairos] Jira backfill on startup: failed: {res.get('error')}")
                else:
                    print(f"[kairos] Jira backfill on startup: ingested={res.get('ingested')}")
            except Exception as exc:
                print(f"[kairos] Jira backfill on startup: error: {exc}")

        jira_backfill_task = asyncio.create_task(_jira_startup_backfill())
    yield
    stop_event.set()
    if poll_task is not None:
        try:
            await poll_task
        except Exception:
            pass
    if teams_task is not None:
        try:
            await teams_task
        except Exception:
            pass
    if jira_backfill_task is not None:
        try:
            await jira_backfill_task
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
