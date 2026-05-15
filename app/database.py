from collections.abc import Generator

from sqlalchemy import text
from sqlmodel import Session, create_engine

from app.config import get_settings


settings = get_settings()


def require_postgres_url(database_url: str) -> None:
    if not database_url.startswith("postgresql"):
        raise ValueError("AgentFlowViz backend is Postgres-only. Set DATABASE_URL to a postgresql+psycopg URL.")


require_postgres_url(settings.database_url)
engine = create_engine(settings.database_url, pool_pre_ping=True)


def init_db() -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
