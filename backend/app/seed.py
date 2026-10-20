"""Demo data generator.

    python -m app.seed            # seed only if the database is empty
    python -m app.seed --reset    # drop everything and reseed

Demo logins (all created here):
    admin@edupulse.ai   / Admin@123
    teacher@edupulse.ai / Teacher@123
    student@edupulse.ai / Student@123
    parent@edupulse.ai  / Parent@123
"""
import argparse
import random
from datetime import date, timedelta

import numpy as np
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .ml import insights
from .ml.model import train
from .models import (
    Assignment, Attendance, Exam, GradingScale, Mark, Remark, SchoolClass, Student, Subject, Submission,
    Teacher, Topic, User,
)
from .security import hash_password
from .services import run_early_warning

SUBJECTS = {
    "Mathematics": ["Algebra", "Trigonometry", "Geometry", "Statistics"],
    "Physics": ["Motion & Forces", "Electricity", "Optics", "Thermodynamics"],
    "Chemistry": ["Chemical Reactions", "Periodic Table", "Acids & Bases", "Organic Chemistry"],
    "English": ["Grammar", "Reading Comprehension", "Essay Writing", "Literature"],
    "Computer Science": ["Programming Basics", "Data Structures", "Algorithms", "Networks"],
}
TEACHERS = [
    ("Meera Iyer", "teacher@edupulse.ai", "Mathematics"),
    ("Daniel Okafor", "d.okafor@edupulse.ai", "Physics"),
    ("Sofia Martinez", "s.martinez@edupulse.ai", "Chemistry"),
    ("Rahul Verma", "r.verma@edupulse.ai", "English"),
    ("Hannah Kim", "h.kim@edupulse.ai", "Computer Science"),
]
CLASSES = [("Grade 10 - A", "A", 10), ("Grade 10 - B", "B", 10), ("Grade 9 - A", "A", 9)]
FIRST = ["Aarav", "Diya", "Kabir", "Ananya", "Vihaan", "Isha", "Arjun", "Sara", "Rohan", "Myra", "Liam", "Emma",
         "Noah", "Olivia", "Ethan", "Ava", "Lucas", "Mia", "Zara", "Omar", "Leah", "Yusuf", "Nina", "Ishaan",
         "Tara", "Kian", "Riya", "Aditya", "Fatima", "Mateo", "Chloe", "Dev", "Anika", "Samuel", "Priya", "Ayaan"]
LAST = ["Sharma", "Patel", "Khan", "Gupta", "Singh", "Reddy", "Nair", "Das", "Brown", "Wilson", "Garcia", "Lopez",
        "Ali", "Chen", "Kumar", "Joshi", "Mehta", "Bose", "Fernandes", "Thomas"]
REMARKS = {
    "praise": ["Excellent participation in class discussions.", "Showed great improvement this term.", "Very helpful to classmates."],
    "academic": ["Needs to revise fundamentals before the next test.", "Careless mistakes are costing marks.", "Should attempt more practice problems."],
    "behaviour": ["Often distracted during lessons.", "Needs to be more punctual with submissions."],
    "attendance": ["Frequent absences are affecting continuity of learning."],
}

STUDENTS_PER_CLASS = 22


class _NoEmail:
    def add_task(self, *args, **kwargs):
        pass


def is_empty(db: Session) -> bool:
    return db.query(User).first() is None


def seed(db: Session) -> None:
    rng = np.random.default_rng(2026)
    random.seed(2026)
    today = date.today()

    db.add(GradingScale(name="Standard (10-point)", bands=insights.DEFAULT_BANDS, is_default=True))
    db.add(GradingScale(name="Pass / Fail", bands=[{"grade": "Pass", "min": 40, "gpa": 1}, {"grade": "Fail", "min": 0, "gpa": 0}]))

    admin = User(email="admin@edupulse.ai", full_name="Ava Thompson", role="admin", password_hash=hash_password("Admin@123"))
    db.add(admin)

    teachers: dict[str, Teacher] = {}
    for i, (name, email, dept) in enumerate(TEACHERS):
        t = Teacher(full_name=name, email=email, department=dept, employee_code=f"T{101 + i}")
        if email == "teacher@edupulse.ai":
            u = User(email=email, full_name=name, role="teacher", password_hash=hash_password("Teacher@123"))
            db.add(u)
            db.flush()
            t.user_id = u.id
        db.add(t)
        teachers[dept] = t
    db.flush()

    used_names: set[str] = set()

    def new_name() -> str:
        while True:
            n = f"{random.choice(FIRST)} {random.choice(LAST)}"
            if n not in used_names:
                used_names.add(n)
                return n

    exam_plan = [("Unit Test 1", "unit_test", 150), ("Mid-Term Exam", "midterm", 105), ("Unit Test 2", "unit_test", 60), ("Pre-Final Exam", "final", 15)]
    school_days = [today - timedelta(days=d) for d in range(80, 0, -1) if (today - timedelta(days=d)).weekday() < 5]

    demo_student = None
    for ci, (cname, section, grade) in enumerate(CLASSES):
        cls = SchoolClass(name=cname, section=section, grade_level=grade, academic_year="2026-27",
                          class_teacher_id=list(teachers.values())[ci % len(teachers)].id)
        db.add(cls)
        db.flush()
        subjects, topics = [], {}
        for sname, tnames in SUBJECTS.items():
            subj = Subject(name=sname, code=f"{sname[:3].upper()}{grade}{section}", class_id=cls.id, teacher_id=teachers[sname].id, pass_mark=40)
            db.add(subj)
            db.flush()
            subjects.append(subj)
            topics[subj.id] = []
            for tn in tnames:
                tp = Topic(subject_id=subj.id, name=tn)
                db.add(tp)
                topics[subj.id].append(tp)
        db.flush()
        exams = []
        for ename, etype, days_ago in exam_plan:
            e = Exam(name=ename, class_id=cls.id, exam_type=etype, date=today - timedelta(days=days_ago), max_marks=100)
            db.add(e)
            exams.append(e)
        db.flush()

        assignments = []
        for k in range(10):
            subj = subjects[k % len(subjects)]
            due = today - timedelta(days=70 - k * 8)
            a = Assignment(title=f"{subj.name}: {random.choice(SUBJECTS[subj.name])} worksheet {k // 5 + 1}",
                           description="Complete all questions and show your working.", class_id=cls.id,
                           subject_id=subj.id, teacher_id=subj.teacher_id, due_date=due, max_score=10)
            db.add(a)
            assignments.append(a)
        db.flush()

        for si in range(STUDENTS_PER_CLASS):
            is_demo = ci == 0 and si == 0
            ability = -0.4 if is_demo else float(rng.normal(0, 1))
            trend = -3.0 if is_demo else float(rng.normal(0.6, 2.0))
            if not is_demo and rng.random() < 0.15:  # a few students in clear decline
                trend -= 4
            att_p = 0.8 if is_demo else float(np.clip(0.9 + 0.05 * ability + rng.normal(0, 0.06), 0.55, 0.995))
            hw_p = 0.66 if is_demo else float(np.clip(0.82 + 0.1 * ability + rng.normal(0, 0.12), 0.25, 1.0))
            name = "Kabir Mehta" if is_demo else new_name()
            email = "student@edupulse.ai" if is_demo else f"{name.lower().replace(' ', '.')}@students.edupulse.ai"
            s = Student(full_name=name, email=email, roll_no=f"{grade}{section}-{si + 1:02d}", class_id=cls.id,
                        gender=random.choice(["Male", "Female"]),
                        date_of_birth=date(2026 - grade - 5, random.randint(1, 12), random.randint(1, 28)),
                        guardian_name=f"{random.choice(FIRST)} {name.split()[1]}",
                        guardian_email=f"guardian.{name.lower().replace(' ', '.')}@example.com",
                        guardian_phone=f"+91 9{random.randint(100000000, 999999999)}")
            if is_demo:
                su = User(email=email, full_name=name, role="student", password_hash=hash_password("Student@123"))
                pu = User(email="parent@edupulse.ai", full_name="Neha Mehta", role="parent", password_hash=hash_password("Parent@123"))
                db.add_all([su, pu])
                db.flush()
                s.user_id, s.parent_user_id = su.id, pu.id
                s.guardian_name, s.guardian_email = pu.full_name, pu.email
                demo_student = s
            db.add(s)
            db.flush()

            subject_off = {subj.id: float(rng.normal(0, 7)) for subj in subjects}
            topic_off = {}
            for subj in subjects:
                for tp in topics[subj.id]:
                    topic_off[tp.id] = float(rng.normal(0, 6))
                weak = random.sample(topics[subj.id], k=1)
                if rng.random() < 0.6:
                    topic_off[weak[0].id] -= 16
            if is_demo:  # make the demo story clear: trouble with Maths and Physics topics
                subject_off[subjects[0].id] -= 10
                subject_off[subjects[1].id] -= 6
                topic_off[topics[subjects[0].id][1].id] -= 18
                topic_off[topics[subjects[1].id][1].id] -= 14

            for ei, exam in enumerate(exams):
                for subj in subjects:
                    for tp in topics[subj.id]:
                        pct = 64 + 14 * ability + trend * ei + subject_off[subj.id] + topic_off[tp.id] + rng.normal(0, 6)
                        pct = float(np.clip(pct, 4, 100))
                        db.add(Mark(student_id=s.id, exam_id=exam.id, subject_id=subj.id, topic_id=tp.id,
                                    score=round(pct / 100 * 25 * 2) / 2, max_score=25))

            late_drop = 0.12 if (trend < -3 and not is_demo) else (0.1 if is_demo else 0)
            for di, d in enumerate(school_days):
                p = att_p - (late_drop if di > len(school_days) * 0.6 else 0)
                r = rng.random()
                status = "present" if r < p else ("late" if r < p + 0.04 else "absent")
                if status == "absent" and rng.random() < 0.15:
                    status = "excused"
                db.add(Attendance(student_id=s.id, class_id=cls.id, date=d, status=status))

            for a in assignments:
                if a.due_date > today:
                    continue
                done = rng.random() < hw_p
                if done:
                    score = float(np.clip(round(6.5 + 2 * ability + rng.normal(0, 1.2)), 0, 10))
                    db.add(Submission(assignment_id=a.id, student_id=s.id, status="graded", score=score))
                else:
                    db.add(Submission(assignment_id=a.id, student_id=s.id, status="missing"))

            if rng.random() < 0.35 or is_demo:
                cat = "academic" if ability < 0 else "praise"
                if att_p < 0.8:
                    cat = "attendance"
                db.add(Remark(student_id=s.id, author_user_id=None, category=cat, text=random.choice(REMARKS[cat])))
        db.flush()

    # Upcoming homework so the dashboards have something "due"
    for cls in db.query(SchoolClass):
        subj = db.query(Subject).filter(Subject.class_id == cls.id).first()
        db.add(Assignment(title=f"{subj.name}: Revision problem set", description="Prepare for the final exam.",
                          class_id=cls.id, subject_id=subj.id, teacher_id=subj.teacher_id, due_date=today + timedelta(days=5), max_score=10))
    if demo_student:
        db.add(Remark(student_id=demo_student.id, author_user_id=None, category="academic",
                      text="Kabir's Trigonometry and Algebra scores have dropped since the mid-term; recommend extra practice."))
    db.commit()

    train(db)
    run_early_warning(db, _NoEmail())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Drop all tables and reseed")
    args = parser.parse_args()
    if args.reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if is_empty(db):
            seed(db)
            print("Seeded demo data.")
        else:
            print("Database already has data; use --reset to reseed.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
