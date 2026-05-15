from fastapi import APIRouter

from app.runtime.rss_rag import create_rss_ingestion_run, execute_rss_ingestion_run, rss_status
from app.runtime.rss_scheduler import rss_scheduler


router = APIRouter(prefix="/rss", tags=["rss"])


@router.get("/status")
def status() -> dict:
    return {"scheduler": rss_scheduler.status(), "rag": rss_status()}


@router.post("/ingest")
def ingest_now() -> dict:
    run_id = create_rss_ingestion_run()
    execute_rss_ingestion_run(run_id)
    return {"started": True, "run_id": run_id}
