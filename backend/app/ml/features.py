"""Load raw student data in bulk and turn it into model features."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from sqlalchemy.orm import Session

from ..models import Assignment, Attendance, Exam, Mark, Student, Subject, Submission, Topic

FEATURES = [
    "avg_pct",
    "recent_pct",
    "trend",
    "attendance_rate",
    "assignment_completion",
    "assignment_avg",
    "fail_subjects",
    "score_std",
]

FEATURE_LABELS = {
    "avg_pct": "Overall average",
    "recent_pct": "Most recent exam",
    "trend": "Score trend",
    "attendance_rate": "Attendance rate",
    "assignment_completion": "Homework completion",
    "assignment_avg": "Homework quality",
    "fail_subjects": "Subjects below pass mark",
    "score_std": "Score consistency",
}

DONE_STATUSES = {"submitted", "late", "graded"}


@dataclass
class StudentData:
    student: Student
    marks: list[Mark] = field(default_factory=list)
    attendance: list[Attendance] = field(default_factory=list)
    submissions: dict[int, Submission] = field(default_factory=dict)  # assignment_id -> submission
    assignments: list[Assignment] = field(default_factory=list)


@dataclass
class Lookup:
    exams: dict[int, Exam]
    subjects: dict[int, Subject]
    topics: dict[int, Topic]


def load_lookup(db: Session) -> Lookup:
    return Lookup(
        exams={e.id: e for e in db.query(Exam)},
        subjects={s.id: s for s in db.query(Subject)},
        topics={t.id: t for t in db.query(Topic)},
    )


def load_student_data(db: Session, students: list[Student]) -> dict[int, StudentData]:
    ids = [s.id for s in students]
    data = {s.id: StudentData(student=s) for s in students}
    if not ids:
        return data
    for m in db.query(Mark).filter(Mark.student_id.in_(ids)):
        data[m.student_id].marks.append(m)
    for a in db.query(Attendance).filter(Attendance.student_id.in_(ids)).order_by(Attendance.date):
        data[a.student_id].attendance.append(a)
    for sub in db.query(Submission).filter(Submission.student_id.in_(ids)):
        data[sub.student_id].submissions[sub.assignment_id] = sub
    class_ids = {s.class_id for s in students if s.class_id}
    by_class: dict[int, list[Assignment]] = defaultdict(list)
    if class_ids:
        for a in db.query(Assignment).filter(Assignment.class_id.in_(class_ids)):
            by_class[a.class_id].append(a)
    for s in students:
        data[s.id].assignments = by_class.get(s.class_id, [])
    return data


def exam_series(sd: StudentData, lk: Lookup) -> list[dict]:
    """Per-exam percentage for a student, ordered by exam date."""
    agg: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for m in sd.marks:
        agg[m.exam_id][0] += m.score
        agg[m.exam_id][1] += m.max_score
    rows = []
    for exam_id, (score, mx) in agg.items():
        exam = lk.exams.get(exam_id)
        if exam is None or mx <= 0:
            continue
        rows.append({"exam_id": exam_id, "exam": exam.name, "date": exam.date, "pct": round(score / mx * 100, 1)})
    rows.sort(key=lambda r: (r["date"] or date.min, r["exam_id"]))
    return rows


def subject_pcts(marks: list[Mark]) -> dict[int, float]:
    agg: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for m in marks:
        agg[m.subject_id][0] += m.score
        agg[m.subject_id][1] += m.max_score
    return {sid: v[0] / v[1] * 100 for sid, v in agg.items() if v[1] > 0}


def attendance_rate(records: list[Attendance]) -> float | None:
    counted = [r for r in records if r.status != "excused"]
    if not counted:
        return None
    score = sum(1.0 if r.status == "present" else 0.5 if r.status == "late" else 0.0 for r in counted)
    return score / len(counted)


def homework_stats(sd: StudentData, today: date | None = None) -> tuple[float | None, float | None, int, int]:
    today = today or date.today()
    due = [a for a in sd.assignments if a.due_date is None or a.due_date <= today]
    if not due:
        return None, None, 0, 0
    done = 0
    scores = []
    for a in due:
        sub = sd.submissions.get(a.id)
        if sub and sub.status in DONE_STATUSES:
            done += 1
            if sub.score is not None and a.max_score:
                scores.append(sub.score / a.max_score)
    return done / len(due), (float(np.mean(scores)) if scores else None), done, len(due)


def compute_features(sd: StudentData, lk: Lookup, marks: list[Mark] | None = None) -> dict:
    marks = sd.marks if marks is None else marks
    view = StudentData(student=sd.student, marks=marks, attendance=sd.attendance,
                       submissions=sd.submissions, assignments=sd.assignments)
    series = exam_series(view, lk)
    total = sum(m.score for m in marks)
    mx = sum(m.max_score for m in marks)
    has_marks = mx > 0
    avg_pct = total / mx * 100 if has_marks else 60.0
    pcts = [r["pct"] for r in series]
    recent = pcts[-1] if pcts else avg_pct
    trend = float(np.polyfit(range(len(pcts)), pcts, 1)[0]) if len(pcts) >= 2 else 0.0
    std = float(np.std(pcts)) if len(pcts) >= 2 else 5.0

    fails = 0
    for sid, pct in subject_pcts(marks).items():
        subj = lk.subjects.get(sid)
        if pct < (subj.pass_mark if subj else 40):
            fails += 1

    att = attendance_rate(sd.attendance)
    completion, hw_avg, _, _ = homework_stats(sd)

    return {
        "avg_pct": round(avg_pct, 2),
        "recent_pct": round(recent, 2),
        "trend": round(trend, 3),
        "attendance_rate": round(att if att is not None else 0.9, 4),
        "assignment_completion": round(completion if completion is not None else 1.0, 4),
        "assignment_avg": round(hw_avg if hw_avg is not None else avg_pct / 100, 4),
        "fail_subjects": fails,
        "score_std": round(std, 3),
        "_has_data": has_marks,
        "_exam_count": len(pcts),
    }


def feature_vector(f: dict) -> list[float]:
    return [float(f[k]) for k in FEATURES]
