from fastapi import APIRouter

from app.telegram_bot import telegram_bot_runner


router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.get("/status")
def telegram_status() -> dict:
    return telegram_bot_runner.status()


@router.post("/start")
def telegram_start() -> dict:
    return telegram_bot_runner.start()


@router.post("/stop")
def telegram_stop() -> dict:
    return telegram_bot_runner.stop()
