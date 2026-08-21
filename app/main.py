from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import get_settings
from app.database import Base, SessionLocal, engine, get_db
from app.middleware import CSRFOriginMiddleware, SecurityHeadersMiddleware, SessionActivityMiddleware
from app.routers import account, auth
from app.services.backup_worker import start_backup_worker, stop_backup_worker
from app.services.bootstrap import bootstrap
from app.services.sftp_tally_sync_worker import (
    start_sftp_tally_sync_worker,
    stop_sftp_tally_sync_worker,
)
from app.services.schema import ensure_runtime_schema
from app.services.sync_worker import start_retry_worker, stop_retry_worker

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.using_default_secret:
        raise RuntimeError(
            "APP_SECRET_KEY is insecure. Set it to a long random string before startup."
        )
    if app.state.app_mode == "lite" and settings.sftp_sync_configuration_error:
        raise RuntimeError(settings.sftp_sync_configuration_error)
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema()
    with SessionLocal() as db:
        bootstrap(db)
    if app.state.app_mode == "legacy":
        start_retry_worker(app)
    elif app.state.app_mode == "lite":
        start_retry_worker(app)
        start_sftp_tally_sync_worker(app)
    start_backup_worker(app)
    try:
        yield
    finally:
        if app.state.app_mode == "legacy":
            await stop_retry_worker(app)
        elif app.state.app_mode == "lite":
            await stop_sftp_tally_sync_worker(app)
            await stop_retry_worker(app)
        await stop_backup_worker(app)


def create_app(app_mode: str | None = None) -> FastAPI:
    settings = get_settings()
    selected_mode = (app_mode or settings.app_mode).strip().lower()
    if selected_mode not in {"lite", "legacy"}:
        raise RuntimeError("Setuora-Lite only supports lite mode.")
    if selected_mode == "legacy" and not settings.allow_legacy_test_mode:
        raise RuntimeError(
            "Legacy direct-Tally mode is test-only in Setuora-Lite. "
            "Production must use lite mode."
        )
    disable_public_docs = selected_mode == "lite"
    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        docs_url=None if disable_public_docs else "/docs",
        redoc_url=None if disable_public_docs else "/redoc",
        openapi_url=None if disable_public_docs else "/openapi.json",
    )
    app.state.app_mode = selected_mode
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    app.add_middleware(SessionActivityMiddleware)
    app.add_middleware(CSRFOriginMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(auth.router)
    app.include_router(account.router)
    from app.routers import (
        audit_assignments,
        barcode_assignment,
        batches,
        dashboard,
        expiry,
        maintenance,
        products,
        replacements,
        reports,
        serials,
        stock_movement,
        users,
        warehouse,
    )

    app.include_router(dashboard.router)
    app.include_router(barcode_assignment.router)
    app.include_router(products.router)
    app.include_router(serials.router)
    app.include_router(audit_assignments.router)
    app.include_router(batches.router)
    app.include_router(reports.router)
    app.include_router(stock_movement.router)
    app.include_router(expiry.router)
    app.include_router(maintenance.router)
    app.include_router(replacements.router)
    app.include_router(users.router)
    app.include_router(warehouse.router)
    if selected_mode == "lite":
        from app.routers import (
            lite_sync,
            receipts,
            settings,
            tally_check,
            transfers,
        )

        app.include_router(lite_sync.router)
        app.include_router(receipts.router)
        app.include_router(settings.router)
        app.include_router(tally_check.router)
        app.include_router(transfers.router)
    else:
        from app.routers import settings, tally_check

        app.include_router(settings.router)
        app.include_router(tally_check.router)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse("app/static/favicon.svg", media_type="image/svg+xml")

    @app.get("/health")
    def health(db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "role": selected_mode}

    return app


app = create_app()
