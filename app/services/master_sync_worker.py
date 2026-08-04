from __future__ import annotations

import asyncio
from contextlib import suppress
import logging

from fastapi import FastAPI

from app.config import get_settings
from app.database import SessionLocal
from app.services.master_sync import (
    MasterSyncError,
    master_sync_enabled,
    poll_master_commands,
    push_pending_events,
)


WORKER_STATE_KEY = "setuora_master_sync_worker_task"
logger = logging.getLogger("setuora")


def sync_with_master_once() -> tuple[int, int]:
    if not master_sync_enabled():
        return 0, 0
    with SessionLocal() as db:
        sent = push_pending_events(db)
        commands = poll_master_commands(db)
        return sent, commands


async def master_sync_worker_loop() -> None:
    last_failure: tuple[str, str] | None = None
    while True:
        interval = max(15, get_settings().master_sync_interval_seconds)
        try:
            await asyncio.to_thread(sync_with_master_once)
        except asyncio.CancelledError:
            raise
        except MasterSyncError as exc:
            failure = ("blocked", str(exc))
            if failure != last_failure:
                logger.warning("Setuora Master synchronization is blocked: %s", exc)
            last_failure = failure
        except Exception as exc:
            failure = ("failed", f"{type(exc).__name__}: {exc}")
            if failure != last_failure:
                logger.exception("Setuora Master synchronization failed")
            last_failure = failure
        else:
            if last_failure is not None:
                logger.info("Setuora Master synchronization recovered")
            last_failure = None
        await asyncio.sleep(interval)


def start_master_sync_worker(app: FastAPI) -> None:
    task = getattr(app.state, WORKER_STATE_KEY, None)
    if task and not task.done():
        return
    setattr(app.state, WORKER_STATE_KEY, asyncio.create_task(master_sync_worker_loop()))


async def stop_master_sync_worker(app: FastAPI) -> None:
    task = getattr(app.state, WORKER_STATE_KEY, None)
    if not task:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    setattr(app.state, WORKER_STATE_KEY, None)
