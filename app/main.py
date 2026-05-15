from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from app.api import agents, rss, runs, system, telegram, workflows
from app.config import get_settings
from app.database import engine, init_db
from app.runtime.rss_scheduler import rss_scheduler
from app.seed import seed_defaults
from app.telegram_bot import telegram_bot_runner


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with Session(engine) as session:
        seed_defaults(session, include_templates=False)
    rss_scheduler.start()
    telegram_bot_runner.start()
    yield
    rss_scheduler.stop()
    telegram_bot_runner.stop()


settings = get_settings()
app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router)
app.include_router(agents.router)
app.include_router(workflows.router)
app.include_router(runs.router)
app.include_router(rss.router)
app.include_router(telegram.router)
