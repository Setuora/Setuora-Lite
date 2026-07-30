from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import require_user
from app.database import get_db
from app.models import Role
from app.services.access_control import (
    ROLE_COLUMNS,
    config_from_form,
    role_access_sections,
    save_role_access_config,
)
from app.services.change_audit import record_change
from app.services.settings import get_all_settings
from app.templates import templates


router = APIRouter(prefix="/settings")


@router.get("/access")
def access_overview(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db, {Role.SUPER_ADMIN})
    return templates.TemplateResponse(
        request,
        "role_access.html",
        {
            "request": request,
            "user": user,
            "roles": ROLE_COLUMNS,
            "sections": role_access_sections(db),
        },
    )


@router.post("/access")
async def save_access_overview(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db, {Role.SUPER_ADMIN})
    form = await request.form()
    before = get_all_settings(db).get("role_access_config", "")
    try:
        save_role_access_config(
            db,
            config_from_form(form.multi_items()),
            commit=False,
        )
        after = get_all_settings(db).get("role_access_config", "")
        record_change(
            db,
            user,
            entity_type="settings",
            entity_id="role_access_config",
            action="update",
            before={"role_access_config": before},
            after={"role_access_config": after},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return RedirectResponse("/settings/access", status_code=303)
