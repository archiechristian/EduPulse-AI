from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..ml import insights, llm
from ..ml.model import get_bundle, train
from ..models import Alert, Student, User
from ..security import accessible_student_ids, get_current_user, require_roles
from ..services import alert_dict, class_context, run_early_warning
from .teacher import scoped_class_ids

router = APIRouter(prefix="/api", tags=["ai"])
staff = require_roles("admin", "teacher")


class ChatTurn(BaseModel):
    role: str
    content: str


class AssistantIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    class_id: int | None = None
    student_id: int | None = None
    history: list[ChatTurn] = []


@router.get("/ai/status")
def status(user: User = Depends(get_current_user)):
    return {"llm_provider": llm.provider(), "smtp_enabled": settings.smtp_enabled}


@router.get("/ai/model-info")
def model_info(user: User = Depends(staff)):
    return get_bundle()["meta"]


@router.post("/ai/retrain")
def retrain(user: User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    return train(db)


@router.post("/ai/early-warning")
def early_warning(background_tasks: BackgroundTasks, class_id: int | None = None,
                  user: User = Depends(staff), db: Session = Depends(get_db)):
    return run_early_warning(db, background_tasks, class_id)


@router.post("/ai/assistant")
def assistant(body: AssistantIn, user: User = Depends(staff), db: Session = Depends(get_db)):
    class_ids = [body.class_id] if body.class_id else scoped_class_ids(db, user)
    if body.student_id:
        s = db.get(Student, body.student_id)
        if s is None:
            raise HTTPException(404, "Student not found")
        class_ids = [s.class_id] if s.class_id else class_ids
    ctx = class_context(db, class_ids, body.student_id)
    history = [h.model_dump() for h in body.history[-8:] if h.role in ("user", "assistant")]
    return {**insights.assistant_reply(body.message, ctx, history), "context": {"scope": ctx["scope"], "summary": ctx["summary"]}}


# ---------------------------------------------------------------- alerts

@router.get("/alerts")
def list_alerts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Alert, Student.full_name).join(Student, Student.id == Alert.student_id)
    allowed = accessible_student_ids(db, user)
    if allowed is not None:
        q = q.filter(Alert.student_id.in_(allowed or [-1]))
    elif user.role == "teacher":
        ids = scoped_class_ids(db, user)
        if ids is not None:
            q = q.filter(Student.class_id.in_(ids))
    return [alert_dict(a, n) for a, n in q.order_by(Alert.created_at.desc()).limit(200)]


@router.post("/alerts/{alert_id}/read")
def mark_read(alert_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = db.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, "Alert not found")
    allowed = accessible_student_ids(db, user)
    if allowed is not None and a.student_id not in allowed:
        raise HTTPException(403, "Not allowed")
    a.is_read = True
    db.commit()
    return {"ok": True}


@router.post("/alerts/read-all")
def mark_all_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Alert).filter(Alert.is_read.is_(False))
    allowed = accessible_student_ids(db, user)
    if allowed is not None:
        q = q.filter(Alert.student_id.in_(allowed or [-1]))
    n = q.update({"is_read": True}, synchronize_session=False)
    db.commit()
    return {"updated": n}
