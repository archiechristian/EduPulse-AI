from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Alert, Assignment, Attendance, Exam, Mark, SchoolClass, Student, Subject, Teacher, User
from ..security import require_roles
from ..services import alert_dict, analyze_students
from .teacher import scoped_class_ids

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
staff = require_roles("admin", "teacher")


def _students(db: Session, user: User, class_id: int | None) -> list[Student]:
    q = db.query(Student)
    if class_id:
        return q.filter(Student.class_id == class_id).all()
    ids = scoped_class_ids(db, user)
    if ids is not None:
        q = q.filter(Student.class_id.in_(ids))
    return q.all()


@router.get("/overview")
def overview(class_id: int | None = None, user: User = Depends(staff), db: Session = Depends(get_db)):
    students = _students(db, user, class_id)
    sids = [s.id for s in students]
    class_ids = sorted({s.class_id for s in students if s.class_id})
    analysed = analyze_students(db, students)
    with_data = [a for a in analysed if a["has_data"]]

    since = date.today() - timedelta(days=30)
    daily = defaultdict(lambda: [0.0, 0])
    if sids:
        for a in db.query(Attendance).filter(Attendance.student_id.in_(sids), Attendance.date >= since, Attendance.status != "excused"):
            daily[a.date][0] += 1.0 if a.status == "present" else 0.5 if a.status == "late" else 0.0
            daily[a.date][1] += 1
    attendance_trend = [{"date": d.isoformat(), "rate": round(v[0] / v[1] * 100, 1)} for d, v in sorted(daily.items()) if v[1]]

    classes = {c.id: c for c in db.query(SchoolClass)}
    per_class = defaultdict(list)
    for a in analysed:
        per_class[a["class_id"]].append(a)
    class_perf = []
    for cid, rows in per_class.items():
        rows_d = [r for r in rows if r["has_data"]]
        class_perf.append({
            "class_id": cid, "class": classes[cid].name if cid in classes else "Unassigned", "students": len(rows),
            "avg_pct": round(sum(r["avg_pct"] for r in rows_d) / max(1, len(rows_d)), 1),
            "attendance": round(sum(r["attendance"] for r in rows) / max(1, len(rows)), 1),
            "at_risk": sum(1 for r in rows_d if r["risk_level"] != "low"),
        })
    class_perf.sort(key=lambda r: r["class"])

    subj_perf = []
    if sids:
        rows = (db.query(Subject.name, func.sum(Mark.score), func.sum(Mark.max_score))
                .join(Subject, Subject.id == Mark.subject_id).filter(Mark.student_id.in_(sids)).group_by(Subject.name))
        subj_perf = sorted([{"subject": n, "avg_pct": round(s / m * 100, 1)} for n, s, m in rows if m], key=lambda r: r["avg_pct"])

    grade_dist = defaultdict(int)
    for a in with_data:
        bucket = min(9, int(a["avg_pct"] // 10))
        grade_dist[bucket] += 1
    distribution = [{"range": f"{b*10}-{b*10+9 if b < 9 else 100}", "students": grade_dist.get(b, 0)} for b in range(10)]

    alerts_q = db.query(Alert, Student.full_name).join(Student, Student.id == Alert.student_id)
    if sids:
        alerts_q = alerts_q.filter(Alert.student_id.in_(sids))
    recent_alerts = [alert_dict(a, n) for a, n in alerts_q.order_by(Alert.created_at.desc()).limit(8)]

    return {
        "counts": {
            "students": len(students),
            "teachers": db.query(func.count(Teacher.id)).scalar(),
            "classes": len(class_ids) if class_ids else db.query(func.count(SchoolClass.id)).scalar(),
            "subjects": db.query(func.count(Subject.id)).filter(Subject.class_id.in_(class_ids)).scalar() if class_ids else 0,
            "exams": db.query(func.count(Exam.id)).filter(Exam.class_id.in_(class_ids)).scalar() if class_ids else 0,
            "assignments": db.query(func.count(Assignment.id)).filter(Assignment.class_id.in_(class_ids)).scalar() if class_ids else 0,
            "users": db.query(func.count(User.id)).scalar(),
        },
        "avg_pct": round(sum(a["avg_pct"] for a in with_data) / max(1, len(with_data)), 1),
        "attendance_rate": round(sum(a["attendance"] for a in analysed) / max(1, len(analysed)), 1),
        "homework_rate": round(sum(a["homework"] for a in analysed) / max(1, len(analysed)), 1),
        "risk": {
            "high": sum(1 for a in with_data if a["risk_level"] == "high"),
            "medium": sum(1 for a in with_data if a["risk_level"] == "medium"),
            "low": sum(1 for a in with_data if a["risk_level"] == "low"),
        },
        "at_risk": [{k: v for k, v in a.items() if not k.startswith("_")} for a in with_data if a["risk_level"] != "low"][:10],
        "class_performance": class_perf,
        "subject_performance": subj_perf,
        "distribution": distribution,
        "attendance_trend": attendance_trend,
        "recent_alerts": recent_alerts,
    }


@router.get("/trends")
def trends(class_id: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    exams = db.query(Exam).filter(Exam.class_id == class_id).order_by(Exam.date, Exam.id).all()
    rows = (
        db.query(Mark.exam_id, Subject.name, func.sum(Mark.score), func.sum(Mark.max_score))
        .join(Subject, Subject.id == Mark.subject_id)
        .filter(Mark.exam_id.in_([e.id for e in exams]))
        .group_by(Mark.exam_id, Subject.name)
    )
    per_exam = defaultdict(dict)
    totals = defaultdict(lambda: [0.0, 0.0])
    subjects = set()
    for exam_id, name, s, m in rows:
        if m:
            per_exam[exam_id][name] = round(s / m * 100, 1)
            totals[exam_id][0] += s
            totals[exam_id][1] += m
            subjects.add(name)
    series = []
    for e in exams:
        if e.id not in per_exam:
            continue
        t = totals[e.id]
        series.append({"exam": e.name, "date": e.date.isoformat() if e.date else None,
                       "overall": round(t[0] / t[1] * 100, 1), **per_exam[e.id]})
    return {"subjects": sorted(subjects), "series": series}


@router.get("/rankings")
def rankings(class_id: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    students = db.query(Student).filter(Student.class_id == class_id).all()
    exams = db.query(Exam).filter(Exam.class_id == class_id).order_by(Exam.date, Exam.id).all()
    exam_ids = [e.id for e in exams]
    agg = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    if students:
        for m in db.query(Mark).filter(Mark.student_id.in_([s.id for s in students])):
            agg[m.student_id][m.exam_id][0] += m.score
            agg[m.student_id][m.exam_id][1] += m.max_score

    def rank_on(exam_id):
        scores = [(v[exam_id][0] / v[exam_id][1] * 100, sid) for sid, v in agg.items() if exam_id in v and v[exam_id][1]]
        return {sid: i for i, (_, sid) in enumerate(sorted(scores, reverse=True), 1)}

    taken = [e for e in exam_ids if any(e in v for v in agg.values())]
    latest = rank_on(taken[-1]) if taken else {}
    previous = rank_on(taken[-2]) if len(taken) >= 2 else {}
    rows = []
    for s in students:
        exams_s = agg.get(s.id, {})
        tot = sum(v[0] for v in exams_s.values())
        mx = sum(v[1] for v in exams_s.values())
        if not mx:
            continue
        last = exams_s.get(taken[-1]) if taken else None
        rows.append({
            "student_id": s.id, "name": s.full_name, "roll_no": s.roll_no, "avg_pct": round(tot / mx * 100, 1),
            "latest_pct": round(last[0] / last[1] * 100, 1) if last and last[1] else None,
            "rank_change": (previous[s.id] - latest[s.id]) if s.id in previous and s.id in latest else 0,
        })
    rows.sort(key=lambda r: -r["avg_pct"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


@router.get("/at-risk")
def at_risk(class_id: int | None = None, user: User = Depends(staff), db: Session = Depends(get_db)):
    analysed = analyze_students(db, _students(db, user, class_id))
    return [{k: v for k, v in a.items() if not k.startswith("_")} for a in analysed if a["has_data"]]
