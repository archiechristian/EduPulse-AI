from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Assignment, Attendance, Exam, Mark, SchoolClass, Student, Subject, Submission, Topic, User, utcnow
from ..security import require_roles, teacher_for

router = APIRouter(prefix="/api/teacher", tags=["teacher"])
staff = require_roles("admin", "teacher")


def scoped_class_ids(db: Session, user: User) -> list[int] | None:
    """Classes a teacher teaches (as class teacher or subject teacher). None = all."""
    if user.role == "admin":
        return None
    t = teacher_for(db, user)
    if t is None:
        return None
    ids = {c.id for c in db.query(SchoolClass.id).filter(SchoolClass.class_teacher_id == t.id)}
    ids |= {s.class_id for s in db.query(Subject.class_id).filter(Subject.teacher_id == t.id) if s.class_id}
    return sorted(ids) or None


@router.get("/scope")
def scope(user: User = Depends(staff), db: Session = Depends(get_db)):
    ids = scoped_class_ids(db, user)
    q = db.query(SchoolClass)
    if ids is not None:
        q = q.filter(SchoolClass.id.in_(ids))
    classes = q.order_by(SchoolClass.grade_level, SchoolClass.name).all()
    class_ids = [c.id for c in classes]
    t = teacher_for(db, user)
    subjects = db.query(Subject).filter(Subject.class_id.in_(class_ids)).all()
    if t is not None and user.role == "teacher":
        own = [s for s in subjects if s.teacher_id == t.id]
        subjects = own or subjects
    return {
        "teacher_id": t.id if t else None,
        "classes": [{"id": c.id, "name": c.name} for c in classes],
        "subjects": [{"id": s.id, "name": s.name, "class_id": s.class_id} for s in subjects],
        "exams": [
            {"id": e.id, "name": e.name, "class_id": e.class_id, "date": e.date.isoformat() if e.date else None, "max_marks": e.max_marks}
            for e in db.query(Exam).filter(Exam.class_id.in_(class_ids)).order_by(Exam.date)
        ],
    }


# ---------------------------------------------------------------- marks

class MarkEntry(BaseModel):
    student_id: int
    topic_id: int | None = None
    score: float | None = None
    max_score: float


class MarksIn(BaseModel):
    exam_id: int
    subject_id: int
    entries: list[MarkEntry]


@router.get("/marks")
def get_marks(exam_id: int, subject_id: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    exam = db.get(Exam, exam_id)
    subject = db.get(Subject, subject_id)
    if not exam or not subject:
        raise HTTPException(404, "Exam or subject not found")
    topics = db.query(Topic).filter(Topic.subject_id == subject_id).order_by(Topic.id).all()
    students = db.query(Student).filter(Student.class_id == exam.class_id).order_by(Student.roll_no, Student.full_name).all()
    marks = db.query(Mark).filter(Mark.exam_id == exam_id, Mark.subject_id == subject_id).all()
    by_student: dict[int, dict] = {}
    for m in marks:
        by_student.setdefault(m.student_id, {})[str(m.topic_id or "total")] = {"score": m.score, "max_score": m.max_score}
    return {
        "exam": {"id": exam.id, "name": exam.name, "max_marks": exam.max_marks},
        "subject": {"id": subject.id, "name": subject.name, "pass_mark": subject.pass_mark},
        "topics": [{"id": t.id, "name": t.name} for t in topics],
        "students": [{"id": s.id, "full_name": s.full_name, "roll_no": s.roll_no, "scores": by_student.get(s.id, {})} for s in students],
    }


@router.put("/marks")
def save_marks(body: MarksIn, user: User = Depends(staff), db: Session = Depends(get_db)):
    existing = {
        (m.student_id, m.topic_id): m
        for m in db.query(Mark).filter(Mark.exam_id == body.exam_id, Mark.subject_id == body.subject_id)
    }
    saved = removed = 0
    for e in body.entries:
        key = (e.student_id, e.topic_id)
        m = existing.get(key)
        if e.score is None:
            if m:
                db.delete(m)
                removed += 1
            continue
        if e.score < 0 or e.score > e.max_score:
            raise HTTPException(422, f"Score {e.score} must be between 0 and {e.max_score}")
        if m is None:
            m = Mark(student_id=e.student_id, exam_id=body.exam_id, subject_id=body.subject_id, topic_id=e.topic_id)
            db.add(m)
        m.score = e.score
        m.max_score = e.max_score
        saved += 1
    db.commit()
    return {"saved": saved, "removed": removed}


# ---------------------------------------------------------------- attendance

class AttendanceRecord(BaseModel):
    student_id: int
    status: str
    note: str | None = None


class AttendanceIn(BaseModel):
    class_id: int
    date: date
    records: list[AttendanceRecord]


@router.get("/attendance")
def get_attendance(class_id: int, date: date, user: User = Depends(staff), db: Session = Depends(get_db)):
    students = db.query(Student).filter(Student.class_id == class_id).order_by(Student.roll_no, Student.full_name).all()
    recs = {a.student_id: a for a in db.query(Attendance).filter(Attendance.class_id == class_id, Attendance.date == date)}
    return [
        {"student_id": s.id, "full_name": s.full_name, "roll_no": s.roll_no,
         "status": recs[s.id].status if s.id in recs else None, "note": recs[s.id].note if s.id in recs else None}
        for s in students
    ]


@router.put("/attendance")
def save_attendance(body: AttendanceIn, user: User = Depends(staff), db: Session = Depends(get_db)):
    valid = {"present", "absent", "late", "excused"}
    existing = {a.student_id: a for a in db.query(Attendance).filter(Attendance.date == body.date,
                                                                       Attendance.student_id.in_([r.student_id for r in body.records]))}
    for r in body.records:
        if r.status not in valid:
            raise HTTPException(422, f"Invalid status '{r.status}'")
        a = existing.get(r.student_id)
        if a is None:
            a = Attendance(student_id=r.student_id, date=body.date)
            db.add(a)
        a.class_id = body.class_id
        a.status = r.status
        a.note = r.note
    db.commit()
    return {"saved": len(body.records)}


# ---------------------------------------------------------------- homework

class SubmissionRecord(BaseModel):
    student_id: int
    status: str
    score: float | None = None
    feedback: str | None = None


class SubmissionsIn(BaseModel):
    records: list[SubmissionRecord]


@router.get("/assignments/{assignment_id}/submissions")
def get_submissions(assignment_id: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    a = db.get(Assignment, assignment_id)
    if a is None:
        raise HTTPException(404, "Assignment not found")
    students = db.query(Student).filter(Student.class_id == a.class_id).order_by(Student.roll_no, Student.full_name).all()
    subs = {s.student_id: s for s in db.query(Submission).filter(Submission.assignment_id == assignment_id)}
    overdue = a.due_date is not None and a.due_date < date.today()
    return {
        "assignment": {"id": a.id, "title": a.title, "max_score": a.max_score, "due_date": a.due_date.isoformat() if a.due_date else None},
        "students": [
            {"student_id": s.id, "full_name": s.full_name, "roll_no": s.roll_no,
             "status": subs[s.id].status if s.id in subs else ("missing" if overdue else "pending"),
             "score": subs[s.id].score if s.id in subs else None,
             "feedback": subs[s.id].feedback if s.id in subs else None}
            for s in students
        ],
    }


@router.put("/assignments/{assignment_id}/submissions")
def save_submissions(assignment_id: int, body: SubmissionsIn, user: User = Depends(staff), db: Session = Depends(get_db)):
    a = db.get(Assignment, assignment_id)
    if a is None:
        raise HTTPException(404, "Assignment not found")
    valid = {"pending", "submitted", "late", "missing", "graded"}
    existing = {s.student_id: s for s in db.query(Submission).filter(Submission.assignment_id == assignment_id)}
    for r in body.records:
        if r.status not in valid:
            raise HTTPException(422, f"Invalid status '{r.status}'")
        if r.score is not None and not 0 <= r.score <= a.max_score:
            raise HTTPException(422, f"Score must be between 0 and {a.max_score}")
        s = existing.get(r.student_id)
        if s is None:
            s = Submission(assignment_id=assignment_id, student_id=r.student_id)
            db.add(s)
        if r.status in ("submitted", "late", "graded") and s.submitted_at is None:
            s.submitted_at = utcnow()
        s.status, s.score, s.feedback = r.status, r.score, r.feedback
    db.commit()
    return {"saved": len(body.records)}


@router.get("/students")
def my_students(q: str | None = None, class_id: int | None = None, user: User = Depends(staff), db: Session = Depends(get_db)):
    ids = scoped_class_ids(db, user)
    query = db.query(Student)
    if class_id:
        query = query.filter(Student.class_id == class_id)
    elif ids is not None:
        query = query.filter(Student.class_id.in_(ids))
    if q:
        query = query.filter(or_(Student.full_name.ilike(f"%{q}%"), Student.roll_no.ilike(f"%{q}%")))
    return [{"id": s.id, "full_name": s.full_name, "roll_no": s.roll_no, "class_id": s.class_id}
            for s in query.order_by(Student.roll_no, Student.full_name).limit(500)]
