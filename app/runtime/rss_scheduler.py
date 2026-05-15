from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import Settings, get_settings
from app.runtime.rss_rag import create_rss_ingestion_run, execute_rss_ingestion_run


class RSSScheduler:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.scheduler: BackgroundScheduler | None = None
        self.last_error = ""

    def start(self) -> None:
        if not self.settings.rss_scheduler_enabled:
            return
        if self.scheduler and self.scheduler.running:
            return

        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(
            self.run_once,
            trigger="interval",
            minutes=max(1, self.settings.rss_crawl_interval_minutes),
            id="rss_rag_ingestion",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(timezone.utc),
        )
        scheduler.start()
        self.scheduler = scheduler

    def stop(self) -> None:
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def run_once(self) -> int | None:
        try:
            run_id = create_rss_ingestion_run()
            execute_rss_ingestion_run(run_id)
            self.last_error = ""
            return run_id
        except Exception as exc:
            self.last_error = str(exc)
            return None

    def status(self) -> dict:
        next_run_at = None
        running = bool(self.scheduler and self.scheduler.running)
        if self.scheduler:
            job = self.scheduler.get_job("rss_rag_ingestion")
            if job and job.next_run_time:
                next_run_at = job.next_run_time.isoformat(timespec="seconds")
        return {
            "enabled": self.settings.rss_scheduler_enabled,
            "running": running,
            "interval_minutes": self.settings.rss_crawl_interval_minutes,
            "next_run_at": next_run_at,
            "last_error": self.last_error,
        }


rss_scheduler = RSSScheduler()
