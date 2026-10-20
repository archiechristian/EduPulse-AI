from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


ROLES = ("admin", "teacher", "student", "parent")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    email_notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    reset_token: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    reset_token_expires: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Teacher(Base):
    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), unique=True)
    full_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    employee_code: Mapped[Optional[str]] = mapped_column(String(50))
    department: Mapped[Optional[str]] = mapped_column(String(120))
    phone: Mapped[Optional[str]] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SchoolClass(Base):
    __tablename__ = "classes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    section: Mapped[Optional[str]] = mapped_column(String(20))
    grade_level: Mapped[Optional[int]] = mapped_column(Integer)
    academic_year: Mapped[Optional[str]] = mapped_column(String(20))
    class_teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))


class Subject(Base):
    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[Optional[str]] = mapped_column(String(30))
    class_id: Mapped[Optional[int]] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), index=True)
    teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"), index=True)
    pass_mark: Mapped[float] = mapped_column(Float, default=40.0)  # percentage


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[Optional[str]] = mapped_column(Text)


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), unique=True)
    full_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    roll_no: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    class_id: Mapped[Optional[int]] = mapped_column(ForeignKey("classes.id", ondelete="SET NULL"), index=True)
    parent_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    gender: Mapped[Optional[str]] = mapped_column(String(20))
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date)
    guardian_name: Mapped[Optional[str]] = mapped_column(String(255))
    guardian_email: Mapped[Optional[str]] = mapped_column(String(255))
    guardian_phone: Mapped[Optional[str]] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Exam(Base):
    __tablename__ = "exams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    class_id: Mapped[Optional[int]] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), index=True)
    exam_type: Mapped[str] = mapped_column(String(40), default="unit_test")
    date: Mapped[Optional[date]] = mapped_column(Date)
    max_marks: Mapped[float] = mapped_column(Float, default=100.0)
    weight: Mapped[float] = mapped_column(Float, default=1.0)


class Mark(Base):
    __tablename__ = "marks"
    __table_args__ = (UniqueConstraint("student_id", "exam_id", "subject_id", "topic_id", name="uq_mark"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    topic_id: Mapped[Optional[int]] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"), index=True)
    score: Mapped[float] = mapped_column(Float)
    max_score: Mapped[float] = mapped_column(Float, default=100.0)
    remarks: Mapped[Optional[str]] = mapped_column(Text)


class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("student_id", "date", name="uq_attendance"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    class_id: Mapped[Optional[int]] = mapped_column(ForeignKey("classes.id", ondelete="SET NULL"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20), default="present")  # present|absent|late|excused
    note: Mapped[Optional[str]] = mapped_column(String(255))


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text)
    class_id: Mapped[Optional[int]] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subjects.id", ondelete="SET NULL"), index=True)
    teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    max_score: Mapped[float] = mapped_column(Float, default=10.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (UniqueConstraint("assignment_id", "student_id", name="uq_submission"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|submitted|late|missing|graded
    score: Mapped[Optional[float]] = mapped_column(Float)
    feedback: Mapped[Optional[str]] = mapped_column(Text)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Remark(Base):
    __tablename__ = "remarks"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    author_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    category: Mapped[str] = mapped_column(String(30), default="general")  # academic|behaviour|attendance|praise|general
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    level: Mapped[str] = mapped_column(String(20), default="info")  # info|warning|critical
    category: Mapped[str] = mapped_column(String(30), default="general")  # risk|attendance|homework|general
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class GradingScale(Base):
    __tablename__ = "grading_scales"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    # [{"grade": "A+", "min": 90, "gpa": 10}, ...] sorted by min desc
    bands: Mapped[list] = mapped_column(JSON, default=list)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class EmailLog(Base):
    __tablename__ = "email_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    to_email: Mapped[str] = mapped_column(String(255), index=True)
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(40), default="general")
    status: Mapped[str] = mapped_column(String(20), default="queued")  # sent|logged|failed
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
