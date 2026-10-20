from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..ml import insights
from ..models import Student, User
from ..security import ensure_student_access, get_current_user
from ..services import student_brief, student_insights

router = APIRouter(prefix="/api/students", tags=["students"])


@router.get("/mine")
def my_students(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The student's own record, or a parent's children."""
    if user.role == "student":
        rows = db.query(Student).filter(Student.user_id == user.id).all()
    elif user.role == "parent":
        rows = db.query(Student).filter(Student.parent_user_id == user.id).all()
    else:
        raise HTTPException(400, "Only students and parents have linked student records")
    return [student_brief(s, db) for s in rows]


@router.post("/link-child")
def link_child(body: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != "parent":
        raise HTTPException(403, "Only parents can link children")
    ident = str(body.get("identifier", "")).strip()
    s = db.query(Student).filter((func.lower(Student.email) == ident.lower()) | (Student.roll_no == ident)).first() if ident else None
    if s is None:
        raise HTTPException(404, "No student found with that email or roll number")
    if s.parent_user_id and s.parent_user_id != user.id:
        raise HTTPException(409, "This student is already linked to another parent account")
    s.parent_user_id = user.id
    db.commit()
    return student_brief(s, db)


@router.get("/{student_id}/insights")
def get_insights(student_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return student_insights(db, ensure_student_access(db, user, student_id))


@router.get("/{student_id}/study-plan")
def get_study_plan(student_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    student = ensure_student_access(db, user, student_id)
    data = student_insights(db, student)
    return insights.study_plan(student.full_name, data["features"], data["prediction"], data["subjects"], data["weak_topics"])


@router.get("/{student_id}/report")
def get_report(student_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    student = ensure_student_access(db, user, student_id)
    data = student_insights(db, student)
    report = insights.ai_report(student.full_name, data["features"], data["prediction"], data["subjects"],
                                data["weak_topics"], data["attendance"], data["homework"])
    return {**report, "student": data["student"], "overall": data["overall"], "prediction": data["prediction"],
            "subjects": data["subjects"], "attendance_rate": data["attendance"]["rate"],
            "homework_rate": data["homework"]["completion_rate"]}
