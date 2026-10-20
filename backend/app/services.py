"""Glue between the database and the ML/insights layer."""
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import emailer
from .ml import insights
from .ml.features import compute_features, load_lookup, load_student_data, subject_pcts
from .ml.model import predict
from .models import Alert, GradingScale, Mark, Remark, SchoolClass, Student, User, utcnow


def get_bands(db: Session) -> list[dict]:
    scale = db.query(GradingScale).filter(GradingScale.is_default.is_(True)).first() or db.query(GradingScale).first()
    return scale.bands if scale and scale.bands else insights.DEFAULT_BANDS


def class_subject_avgs(db: Session, class_id: int | None) -> dict[int, float]:
    if not class_id:
        return {}
    rows = (
        db.query(Mark.subject_id, func.sum(Mark.score), func.sum(Mark.max_score))
        .join(Student, Student.id == Mark.student_id)
        .filter(Student.class_id == class_id)
        .group_by(Mark.subject_id)
    )
    return {sid: s / m * 100 for sid, s, m in rows if m}


def student_brief(s: Student, db: Session) -> dict:
    cls = db.get(SchoolClass, s.class_id) if s.class_id else None
    return {
        "id": s.id, "full_name": s.full_name, "email": s.email, "roll_no": s.roll_no,
        "class_id": s.class_id, "class_name": f"{cls.name}" if cls else None,
        "guardian_name": s.guardian_name, "user_id": s.user_id, "parent_user_id": s.parent_user_id,
    }


def student_insights(db: Session, student: Student) -> dict:
    lk = load_lookup(db)
    sd = load_student_data(db, [student])[student.id]
    bands = get_bands(db)
    features = compute_features(sd, lk)
    prediction = predict(features)
    subjects = insights.subject_performance(sd, lk, bands, class_subject_avgs(db, student.class_id))
    topics = insights.weak_topics(sd, lk)
    attendance = insights.attendance_analysis(sd)
    homework = insights.homework_summary(sd)
    for item in homework["items"]:
        subj = lk.subjects.get(item["subject_id"]) if item["subject_id"] else None
        item["subject"] = subj.name if subj else None
    remarks = (
        db.query(Remark, User.full_name)
        .outerjoin(User, User.id == Remark.author_user_id)
        .filter(Remark.student_id == student.id)
        .order_by(Remark.created_at.desc())
        .limit(20)
        .all()
    )
    alerts = db.query(Alert).filter(Alert.student_id == student.id).order_by(Alert.created_at.desc()).limit(20).all()

    # Rank within class
    rank = None
    class_size = None
    if student.class_id:
        classmates = db.query(Student).filter(Student.class_id == student.class_id).all()
        class_data = load_student_data(db, classmates)
        averages = []
        for sid, d in class_data.items():
            tot = sum(m.score for m in d.marks)
            mx = sum(m.max_score for m in d.marks)
            if mx:
                averages.append((tot / mx, sid))
        averages.sort(reverse=True)
        class_size = len(classmates)
        for i, (_, sid) in enumerate(averages, 1):
            if sid == student.id:
                rank = i

    return {
        "student": student_brief(student, db),
        "features": {k: v for k, v in features.items() if not k.startswith("_")},
        "has_data": features["_has_data"],
        "overall": {
            "avg_pct": features["avg_pct"],
            "grade": insights.grade_for(features["avg_pct"], bands) if features["_has_data"] else None,
            "rank": rank,
            "class_size": class_size,
            "exam_count": features["_exam_count"],
        },
        "prediction": prediction,
        "subjects": subjects,
        "weak_subjects": [s for s in subjects if s["status"] in ("weak", "failing")],
        "weak_topics": topics,
        "attendance": attendance,
        "homework": homework,
        "growth": insights.growth_timeline(sd, lk),
        "remarks": [
            {"id": r.id, "category": r.category, "text": r.text, "author": name, "created_at": r.created_at.isoformat()}
            for r, name in remarks
        ],
        "alerts": [alert_dict(a) for a in alerts],
    }


def alert_dict(a: Alert, student_name: str | None = None) -> dict:
    return {
        "id": a.id, "student_id": a.student_id, "student_name": student_name, "level": a.level, "category": a.category,
        "title": a.title, "message": a.message, "is_read": a.is_read, "created_at": a.created_at.isoformat(),
    }


def analyze_students(db: Session, students: list[Student]) -> list[dict]:
    lk = load_lookup(db)
    data = load_student_data(db, students)
    classes = {c.id: c.name for c in db.query(SchoolClass)}
    out = []
    for s in students:
        sd = data[s.id]
        f = compute_features(sd, lk)
        p = predict(f)
        out.append({
            "student_id": s.id, "name": s.full_name, "roll_no": s.roll_no, "class_id": s.class_id,
            "class_name": classes.get(s.class_id), "avg_pct": f["avg_pct"], "recent_pct": f["recent_pct"],
            "trend": f["trend"], "attendance": round(f["attendance_rate"] * 100, 1),
            "homework": round(f["assignment_completion"] * 100, 1), "has_data": f["_has_data"],
            "_weak_topics": insights.weak_topics(sd, lk, limit=20), **p,
        })
    out.sort(key=lambda r: -r["risk_probability"])
    return out


def class_context(db: Session, class_ids: list[int] | None, student_id: int | None = None) -> dict:
    q = db.query(Student)
    if class_ids:
        q = q.filter(Student.class_id.in_(class_ids))
    students = q.all()
    analysed = analyze_students(db, students)
    n = len(analysed) or 1
    topic_stats: dict[tuple, list] = defaultdict(list)
    lk = load_lookup(db)
    data = load_student_data(db, students)
    for sd in data.values():
        agg: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for m in sd.marks:
            if m.topic_id:
                agg[m.topic_id][0] += m.score
                agg[m.topic_id][1] += m.max_score
        for tid, (s, mx) in agg.items():
            if mx and tid in lk.topics:
                t = lk.topics[tid]
                subj = lk.subjects.get(t.subject_id)
                topic_stats[(t.name, subj.name if subj else "")].append(s / mx * 100)
    class_topics = sorted(
        [
            {"topic": k[0], "subject": k[1], "avg_pct": round(sum(v) / len(v), 1),
             "share_weak": round(sum(1 for x in v if x < 60) / len(v), 2)}
            for k, v in topic_stats.items() if v
        ],
        key=lambda r: r["avg_pct"],
    )
    names = [c.name for c in db.query(SchoolClass).filter(SchoolClass.id.in_(class_ids))] if class_ids else ["All classes"]
    with_data = [a for a in analysed if a["has_data"]]
    ctx = {
        "scope": ", ".join(names),
        "summary": {
            "students": len(analysed),
            "avg_pct": round(sum(a["avg_pct"] for a in with_data) / max(1, len(with_data)), 1),
            "attendance": round(sum(a["attendance"] for a in analysed) / n, 1),
            "homework": round(sum(a["homework"] for a in analysed) / n, 1),
            "high_risk": sum(1 for a in analysed if a["risk_level"] == "high"),
            "medium_risk": sum(1 for a in analysed if a["risk_level"] == "medium"),
        },
        "at_risk": [
            {k: v for k, v in a.items() if not k.startswith("_")}
            for a in analysed if a["risk_level"] in ("high", "medium") and a["has_data"]
        ][:12],
        "class_weak_topics": class_topics[:8],
        "top_performers": [a["name"] for a in sorted(with_data, key=lambda a: -a["avg_pct"])[:5]],
    }
    if student_id:
        st = next((a for a in analysed if a["student_id"] == student_id), None)
        if st:
            ctx["student"] = {"name": st["name"], "predicted_score": st["predicted_score"], "risk_level": st["risk_level"],
                              "reasons": st["reasons"], "weak_topics": st["_weak_topics"][:6],
                              "attendance": st["attendance"], "homework": st["homework"], "avg_pct": st["avg_pct"]}
    return ctx


def run_early_warning(db: Session, background_tasks, class_id: int | None = None) -> dict:
    q = db.query(Student)
    if class_id:
        q = q.filter(Student.class_id == class_id)
    students = {s.id: s for s in q.all()}
    results = analyze_students(db, list(students.values()))
    since = utcnow() - timedelta(days=7)
    created = emailed = 0
    for r in results:
        if not r["has_data"]:
            continue
        s = students[r["student_id"]]
        new_alerts = []
        if r["risk_level"] in ("high", "medium"):
            level = "critical" if r["risk_level"] == "high" else "warning"
            new_alerts.append(("risk", level, f"{r['risk_level'].title()} failure risk detected",
                               f"Predicted next exam score: {r['predicted_score']}% (risk {r['risk_probability']*100:.0f}%)."))
        if r["attendance"] < 75:
            new_alerts.append(("attendance", "warning", "Low attendance", f"Attendance is {r['attendance']}%, below the 75% minimum."))
        if r["homework"] < 60:
            new_alerts.append(("homework", "warning", "Homework not being completed", f"Only {r['homework']}% of homework has been submitted."))
        for category, level, title, message in new_alerts:
            dup = db.query(Alert).filter(Alert.student_id == s.id, Alert.category == category, Alert.created_at >= since).first()
            if dup:
                continue
            db.add(Alert(student_id=s.id, level=level, category=category, title=title, message=message))
            created += 1
            reasons = [x["text"] for x in r["reasons"] if x["type"] == "negative"]
            recipients = []
            if s.parent_user_id:
                parent = db.get(User, s.parent_user_id)
                if parent and parent.email_notifications:
                    recipients.append((parent.full_name, parent.email))
            elif s.guardian_email:
                recipients.append((s.guardian_name or "Parent/Guardian", s.guardian_email))
            if s.user_id:
                su = db.get(User, s.user_id)
                if su and su.email_notifications:
                    recipients.append((su.full_name, su.email))
            for name, email in recipients:
                subject, body = emailer.risk_alert_email(name, s.full_name, title, message, reasons)
                emailer.send_email(background_tasks, email, subject, body, kind="alert")
                emailed += 1
    db.commit()
    return {
        "analysed": len(results),
        "high_risk": sum(1 for r in results if r["risk_level"] == "high" and r["has_data"]),
        "medium_risk": sum(1 for r in results if r["risk_level"] == "medium" and r["has_data"]),
        "alerts_created": created,
        "emails_queued": emailed,
    }
