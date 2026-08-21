from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from fastapi import FastAPI

from app.config import get_settings
from app.database import SessionLocal
from app.services.sftp_tally_sync import (
    SftpTallySyncError,
    run_sftp_tally_sync_cycle,
)

WORKER_STATE_KEY = "setuora_sftp_tally_sync_worker_task"
logger = logging.getLogger("setuora")


def sync_tally_with_master_once() -> str:
    with SessionLocal() as db:
        return run_sftp_tally_sync_cycle(db)


async def sftp_tally_sync_worker_loop() -> None:
    last_failure: str | None = None
    while True:
        interval = max(15, get_settings().sftp_sync_interval_seconds)
        try:
            await asyncio.to_thread(sync_tally_with_master_once)
        except asyncio.CancelledError:
            raise
        except SftpTallySyncError as exc:
            failure = str(exc)
            if failure != last_failure:
                logger.warning("Tally SFTP synchronization is paused: %s", exc)
            last_failure = failure
        except Exception:
            if last_failure != "unexpected":
                logger.exception("Tally SFTP synchronization failed")
            last_failure = "unexpected"
        else:
            if last_failure is not None:
                logger.info("Tally SFTP synchronization recovered")
            last_failure = None
        await asyncio.sleep(interval)


def start_sftp_tally_sync_worker(app: FastAPI) -> None:
    if not get_settings().sftp_sync_enabled:
        return
    task = getattr(app.state, WORKER_STATE_KEY, None)
    if task and not task.done():
        return
    setattr(
        app.state,
        WORKER_STATE_KEY,
        asyncio.create_task(sftp_tally_sync_worker_loop()),
    )


async def stop_sftp_tally_sync_worker(app: FastAPI) -> None:
    task = getattr(app.state, WORKER_STATE_KEY, None)
    if not task:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    setattr(app.state, WORKER_STATE_KEY, None)
