from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import require_permission, require_user
from app.config import get_settings
from app.database import get_db
from app.models import Batch, InventoryTransaction, Role, ScanLog, Serial, StockRelocation, TallyMasterConfirmation, User, serialize_role_values, utc_now
from app.security import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
    password_policy_error,
)
from app.services.change_audit import record_change
from app.services.settings import list_companies
from app.services.tally_access import (
    access_page_data,
    replace_user_access,
    user_access_snapshot,
)
from app.templates import templates

router = APIRouter(prefix="/users")


def _app_mode(request: Request) -> str:
    app = request.scope.get("app")
    return getattr(getattr(app, "state", None), "app_mode", get_settings().app_mode)


def _users_context(
    request: Request,
    current: User,
    db: Session,
    *,
    error: str | None = None,
    success: str | None = None,
) -> dict[str, object]:
    users = db.scalars(
        select(User).where(User.deleted_at.is_(None)).order_by(User.username)
    ).all()
    context = {
        "request": request,
        "user": current,
        "users": users,
        "roles": list(Role),
        "companies": [],
        "error": error,
        "success": success,
    }
    if _app_mode(request) != "lite":
        context["companies"] = list_companies(db)
        context.update(access_page_data(db, users))
    return context


@router.get("")
def users_page(request: Request, error: str = "", success: str = "", db: Session = Depends(get_db)):
    user = require_permission(request, db, "users_manage")
    error_message = {
        "user_delete_self": "You cannot delete your own account",
        "password_reset_self": "Use Change password in your account menu to update your own password.",
        "password_too_short": f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
        "password_too_long": f"Password must be at most {MAX_PASSWORD_LENGTH} characters.",
        "password_mismatch": "Password and confirmation do not match.",
        "username_invalid": "Username must contain 1 to 80 characters.",
        "user_not_found": "User account was not found.",
        "role_required": "Select at least one role.",
        "tally_access_super_admin": "Super admins always have access to all Tally data.",
    }.get(error, error)
    success_message = {
        "password_reset": "Password reset successfully.",
        "tally_access_saved": "Tally access assignments saved.",
    }.get(success, success)
    return templates.TemplateResponse(
        request,
        "users.html",
        _users_context(
            request,
            user,
            db,
            error=error_message or None,
            success=success_message or None,
        ),
    )


@router.post("")
def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    user = require_permission(request, db, "users_manage")
    normalized_username = username.strip().lower()
    if not normalized_username or len(normalized_username) > 80:
        return RedirectResponse("/users?error=username_invalid", status_code=303)
    policy_error = password_policy_error(password)
    if policy_error:
        error_code = (
            "password_too_short"
            if len(password) < MIN_PASSWORD_LENGTH
            else "password_too_long"
        )
        return RedirectResponse(f"/users?error={error_code}", status_code=303)
    role_value = serialize_role_values(role)
    if not role_value:
        return RedirectResponse("/users?error=role_required", status_code=303)
    db.add(
        User(
            username=normalized_username,
            password_hash=hash_password(password),
            role=role_value,
            active=True,
        )
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        return templates.TemplateResponse(
            request,
            "users.html",
            _users_context(
                request,
                user,
                db,
                error="Username already exists",
            ),
            status_code=400,
        )
    return RedirectResponse("/users", status_code=303)


@router.post("/{user_id}/tally-access")
def update_tally_access(
    request: Request,
    user_id: int,
    company_id: list[int] = Form(default=[]),
    ledger_id: list[int] = Form(default=[]),
    tally_user: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    current = require_permission(request, db, "users_manage")
    if _app_mode(request) == "lite":
        raise HTTPException(status_code=404, detail="Tally access is managed on Setuora Master.")
    target = db.get(User, user_id)
    if not target or target.deleted_at:
        return RedirectResponse("/users?error=user_not_found", status_code=303)
    try:
        before = user_access_snapshot(db, target.id)
        replace_user_access(
            db,
            target,
            company_ids=company_id,
            ledger_ids=ledger_id,
            tally_user_values=tally_user,
            commit=False,
        )
        record_change(
            db,
            current,
            entity_type="user_tally_access",
            entity_id=target.id,
            action="update",
            before=before,
            after=user_access_snapshot(db, target.id),
        )
        db.commit()
    except ValueError:
        db.rollback()
        return RedirectResponse(
            "/users?error=tally_access_super_admin",
            status_code=303,
        )
    return RedirectResponse("/users?success=tally_access_saved", status_code=303)


@router.post("/{user_id}/toggle")
def toggle_user(request: Request, user_id: int, db: Session = Depends(get_db)):
    current = require_permission(request, db, "users_manage")
    target = db.get(User, user_id)
    if target and target.id != current.id and not target.deleted_at:
        target.active = not target.active
        target.session_version += 1
        db.commit()
    return RedirectResponse("/users", status_code=303)


@router.post("/{user_id}/password")
def reset_user_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    force_change: str | None = Form(None),
    db: Session = Depends(get_db),
):
    current = require_user(request, db, {Role.SUPER_ADMIN})
    target = db.get(User, user_id)
    if not target or target.deleted_at:
        return RedirectResponse("/users?error=user_not_found", status_code=303)
    if target.id == current.id:
        return RedirectResponse("/users?error=password_reset_self", status_code=303)
    policy_error = password_policy_error(new_password)
    if policy_error:
        error_code = (
            "password_too_short"
            if len(new_password) < MIN_PASSWORD_LENGTH
            else "password_too_long"
        )
        return RedirectResponse(f"/users?error={error_code}", status_code=303)
    if new_password != confirm_password:
        return RedirectResponse("/users?error=password_mismatch", status_code=303)

    target.password_hash = hash_password(new_password)
    target.must_change_password = force_change == "true"
    target.session_version += 1
    db.commit()
    return RedirectResponse("/users?success=password_reset", status_code=303)


@router.post("/{user_id}/delete")
def delete_user(request: Request, user_id: int, db: Session = Depends(get_db)):
    current = require_user(request, db, {Role.SUPER_ADMIN})
    target = db.get(User, user_id)
    if not target:
        return RedirectResponse("/users", status_code=303)
    if target.id == current.id:
        return RedirectResponse("/users?error=user_delete_self", status_code=303)

    reference_count = sum(
        (
            db.scalar(select(func.count(Batch.id)).where(Batch.user_id == target.id)) or 0,
            db.scalar(select(func.count(InventoryTransaction.id)).where(InventoryTransaction.user_id == target.id)) or 0,
            db.scalar(select(func.count(ScanLog.id)).where(ScanLog.user_id == target.id)) or 0,
            db.scalar(select(func.count(Serial.id)).where(Serial.label_printed_by_id == target.id)) or 0,
            db.scalar(select(func.count(TallyMasterConfirmation.id)).where(TallyMasterConfirmation.confirmed_by_id == target.id)) or 0,
            db.scalar(select(func.count(StockRelocation.id)).where(StockRelocation.user_id == target.id)) or 0,
        )
    )
    if reference_count:
        target.active = False
        target.deleted_at = utc_now()
        db.commit()
        return RedirectResponse("/users", status_code=303)

    db.delete(target)
    db.commit()
    return RedirectResponse("/users", status_code=303)
