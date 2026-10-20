"""Generic, metadata-driven CRUD so admins can add / edit / remove every entity.

GET    /api/crud/{resource}?<column>=<value>&q=<search>
POST   /api/crud/{resource}
PATCH  /api/crud/{resource}/{id}
DELETE /api/crud/{resource}/{id}
"""
import json
import secrets
from datetime import date, datetime

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Request
from sqlalchemy import JSON, Boolean, Date, DateTime, Float, Integer, String, Text, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import emailer
from ..database import get_db
from ..models import (
    Alert, Assignment, Attendance, EmailLog, Exam, GradingScale, Mark, Remark, SchoolClass, Student,
    Subject, Submission, Teacher, Topic, User,
)
from ..security import get_current_user, hash_password

router = APIRouter(prefix="/api/crud", tags=["crud"])

STAFF = ("admin", "teacher")
RESOURCES: dict[str, dict] = {
    "users": {"model": User, "read": ("admin",), "write": ("admin",), "hidden": {"password_hash", "reset_token", "reset_token_expires"}},
    "teachers": {"model": Teacher, "read": STAFF, "write": ("admin",)},
    "classes": {"model": SchoolClass, "read": STAFF, "write": ("admin",), "order": "grade_level"},
    "subjects": {"model": Subject, "read": STAFF, "write": ("admin",)},
    "topics": {"model": Topic, "read": STAFF, "write": STAFF},
    "students": {"model": Student, "read": STAFF, "write": ("admin",), "order": "roll_no"},
    "exams": {"model": Exam, "read": STAFF, "write": ("admin",), "order": "date"},
    "marks": {"model": Mark, "read": STAFF, "write": STAFF},
    "attendance": {"model": Attendance, "read": STAFF, "write": STAFF},
    "assignments": {"model": Assignment, "read": STAFF, "write": STAFF, "order": "-due_date"},
    "submissions": {"model": Submission, "read": STAFF, "write": STAFF},
    "remarks": {"model": Remark, "read": STAFF, "write": STAFF, "order": "-created_at"},
    "alerts": {"model": Alert, "read": STAFF, "write": ("admin",), "order": "-created_at"},
    "grading-scales": {"model": GradingScale, "read": STAFF, "write": ("admin",)},
    "emails": {"model": EmailLog, "read": ("admin",), "write": (), "order": "-created_at"},
}
READONLY_COLUMNS = {"id", "created_at"}


def _resource(name: str, user: User, write: bool = False) -> dict:
    res = RESOURCES.get(name)
    if res is None:
        raise HTTPException(404, f"Unknown resource '{name}'")
    if user.role not in (res["write"] if write else res["read"]):
        raise HTTPException(403, "You do not have permission for this action")
    return res


def _columns(model):
    return {c.name: c for c in model.__table__.columns}


def _coerce(col, value):
    if value is None:
        return None
    if value == "" and (col.nullable or not isinstance(col.type, (String, Text))):
        if not col.nullable:
            raise HTTPException(422, f"'{col.name}' is required")
        return None
    t = col.type
    try:
        if isinstance(t, Boolean):
            return value in (True, 1, "1", "true", "True", "on", "yes")
        if isinstance(t, Integer):
            return int(value)
        if isinstance(t, Float):
            return float(value)
        if isinstance(t, DateTime):
            return value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", ""))
        if isinstance(t, Date):
            return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
        if isinstance(t, JSON):
            return json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(422, f"Invalid value for '{col.name}'")
    return str(value) if value is not None else None


def serialize(obj, hidden: set[str] | None = None) -> dict:
    out = {}
    for name in _columns(type(obj)):
        if hidden and name in hidden:
            continue
        v = getattr(obj, name)
        out[name] = v.isoformat() if isinstance(v, (date, datetime)) else v
    return out


def _apply(obj, payload: dict, res: dict):
    cols = _columns(res["model"])
    for key, value in payload.items():
        if key in READONLY_COLUMNS or key not in cols or key in res.get("hidden", set()):
            continue
        setattr(obj, key, _coerce(cols[key], value))


def _commit(db: Session):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        msg = str(exc.orig).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(409, "A record with these values already exists")
        if "foreign key" in msg:
            raise HTTPException(409, "This record references (or is referenced by) another record")
        if "not null" in msg:
            raise HTTPException(422, "A required field is missing")
        raise HTTPException(409, "Could not save record")


@router.get("/{resource}")
def list_items(resource: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    res = _resource(resource, user)
    model = res["model"]
    cols = _columns(model)
    q = db.query(model)
    params = request.query_params
    for key, value in params.items():
        if key in cols and value != "":
            q = q.filter(cols[key] == _coerce(cols[key], value))
    if params.get("q"):
        term = f"%{params['q'].lower()}%"
        text_cols = [c for c in cols.values() if isinstance(c.type, (String, Text)) and c.name not in res.get("hidden", set())]
        if text_cols:
            q = q.filter(or_(*[func.lower(c).like(term) for c in text_cols]))
    order = res.get("order", "id")
    desc = order.startswith("-")
    col = cols[order.lstrip("-")]
    q = q.order_by(col.desc() if desc else col.asc(), cols["id"].asc())
    limit = min(int(params.get("limit", 1000)), 5000)
    return [serialize(o, res.get("hidden")) for o in q.limit(limit)]


@router.get("/{resource}/{item_id}")
def get_item(resource: str, item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    res = _resource(resource, user)
    obj = db.get(res["model"], item_id)
    if obj is None:
        raise HTTPException(404, "Not found")
    return serialize(obj, res.get("hidden"))


@router.post("/{resource}", status_code=201)
def create_item(resource: str, background_tasks: BackgroundTasks, payload: dict = Body(...),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    res = _resource(resource, user, write=True)
    model = res["model"]
    obj = model()
    temp_password = None
    if model is User:
        if not payload.get("email") or not payload.get("full_name") or payload.get("role") not in ("admin", "teacher", "student", "parent"):
            raise HTTPException(422, "full_name, email and a valid role are required")
        payload["email"] = str(payload["email"]).lower().strip()
        password = payload.pop("password", None)
        if not password:
            temp_password = password = secrets.token_urlsafe(8)
        obj.password_hash = hash_password(password)
    if model is Remark and not payload.get("author_user_id"):
        payload["author_user_id"] = user.id
    if model is Assignment and not payload.get("teacher_id") and user.role == "teacher":
        t = db.query(Teacher).filter(Teacher.user_id == user.id).first()
        payload["teacher_id"] = t.id if t else None
    _apply(obj, payload, res)
    db.add(obj)
    _commit(db)
    if model is User:
        _link_profile(db, obj)
        subject, body = emailer.welcome_email(obj.full_name, obj.role, temp_password)
        emailer.send_email(background_tasks, obj.email, subject, body, kind="welcome")
    if model is GradingScale and obj.is_default:
        _single_default(db, obj)
    return serialize(obj, res.get("hidden"))


@router.patch("/{resource}/{item_id}")
def update_item(resource: str, item_id: int, payload: dict = Body(...),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    res = _resource(resource, user, write=True)
    obj = db.get(res["model"], item_id)
    if obj is None:
        raise HTTPException(404, "Not found")
    if res["model"] is User and payload.get("password"):
        obj.password_hash = hash_password(str(payload.pop("password")))
    _apply(obj, payload, res)
    _commit(db)
    if res["model"] is GradingScale and obj.is_default:
        _single_default(db, obj)
    return serialize(obj, res.get("hidden"))


@router.delete("/{resource}/{item_id}")
def delete_item(resource: str, item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    res = _resource(resource, user, write=True)
    obj = db.get(res["model"], item_id)
    if obj is None:
        raise HTTPException(404, "Not found")
    if res["model"] is User and obj.id == user.id:
        raise HTTPException(400, "You cannot delete your own account")
    db.delete(obj)
    _commit(db)
    return {"ok": True}


def _single_default(db: Session, keep: GradingScale):
    db.query(GradingScale).filter(GradingScale.id != keep.id).update({"is_default": False})
    db.commit()


def _link_profile(db: Session, u: User):
    """When an admin creates a login for an existing student/teacher record, link them by email."""
    if u.role == "student":
        s = db.query(Student).filter(func.lower(Student.email) == u.email, Student.user_id.is_(None)).first()
        if s:
            s.user_id = u.id
    elif u.role == "teacher":
        t = db.query(Teacher).filter(func.lower(Teacher.email) == u.email, Teacher.user_id.is_(None)).first()
        if t:
            t.user_id = u.id
    db.commit()
