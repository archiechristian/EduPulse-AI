import secrets
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import emailer
from ..config import settings
from ..database import get_db
from ..models import SchoolClass, Student, Teacher, User, utcnow
from ..security import create_access_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

Role = Literal["admin", "teacher", "student", "parent"]


class SignupIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: Role
    phone: str | None = None
    # student
    class_id: int | None = None
    roll_no: str | None = None
    # parent: child's email or roll number
    child_identifier: str | None = None
    # teacher
    department: str | None = None
    # admin
    admin_code: str | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str
    role: Role | None = None


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=128)


class ProfileIn(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=255)
    phone: str | None = None
    email_notifications: bool | None = None


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


def user_payload(db: Session, user: User) -> dict:
    data = {
        "id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role, "phone": user.phone,
        "email_notifications": user.email_notifications, "created_at": user.created_at.isoformat(),
        "student_id": None, "teacher_id": None, "children": [],
    }
    if user.role == "student":
        s = db.query(Student).filter(Student.user_id == user.id).first()
        data["student_id"] = s.id if s else None
    elif user.role == "teacher":
        t = db.query(Teacher).filter(Teacher.user_id == user.id).first()
        data["teacher_id"] = t.id if t else None
    elif user.role == "parent":
        data["children"] = [
            {"id": s.id, "full_name": s.full_name, "class_id": s.class_id}
            for s in db.query(Student).filter(Student.parent_user_id == user.id)
        ]
    return data


@router.get("/options")
def signup_options(db: Session = Depends(get_db)):
    classes = db.query(SchoolClass).order_by(SchoolClass.grade_level, SchoolClass.name).all()
    return {
        "classes": [{"id": c.id, "label": c.name} for c in classes],
        "admin_signup_enabled": bool(settings.ADMIN_INVITE_CODE),
    }


@router.post("/signup")
def signup(body: SignupIn, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.query(User).filter(func.lower(User.email) == email).first():
        raise HTTPException(409, "An account with this email already exists")
    if body.role == "admin" and (not settings.ADMIN_INVITE_CODE or body.admin_code != settings.ADMIN_INVITE_CODE):
        raise HTTPException(403, "A valid admin invite code is required to create an admin account")

    child = None
    if body.role == "parent":
        ident = (body.child_identifier or "").strip()
        if not ident:
            raise HTTPException(400, "Enter your child's student email or roll number to link your account")
        child = db.query(Student).filter(or_(func.lower(Student.email) == ident.lower(), Student.roll_no == ident)).first()
        if child is None:
            raise HTTPException(404, "No student found with that email or roll number")

    user = User(email=email, full_name=body.full_name.strip(), password_hash=hash_password(body.password),
                role=body.role, phone=body.phone)
    db.add(user)
    db.flush()

    if body.role == "student":
        student = db.query(Student).filter(func.lower(Student.email) == email, Student.user_id.is_(None)).first()
        if student is None:
            if body.class_id and not db.get(SchoolClass, body.class_id):
                raise HTTPException(400, "Selected class does not exist")
            student = Student(full_name=user.full_name, email=email, class_id=body.class_id, roll_no=body.roll_no)
            db.add(student)
        student.user_id = user.id
    elif body.role == "teacher":
        teacher = db.query(Teacher).filter(func.lower(Teacher.email) == email, Teacher.user_id.is_(None)).first()
        if teacher is None:
            teacher = Teacher(full_name=user.full_name, email=email, department=body.department, phone=body.phone)
            db.add(teacher)
        teacher.user_id = user.id
    elif body.role == "parent" and child is not None:
        child.parent_user_id = user.id
        if not child.guardian_name:
            child.guardian_name = user.full_name
            child.guardian_email = email

    db.commit()
    subject, html_body = emailer.welcome_email(user.full_name, user.role)
    emailer.send_email(background_tasks, user.email, subject, html_body, kind="welcome")
    return {"token": create_access_token(user), "user": user_payload(db, user)}


@router.post("/login")
def login(body: LoginIn, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user = db.query(User).filter(func.lower(User.email) == body.email.lower()).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    if not user.is_active:
        raise HTTPException(403, "This account has been disabled")
    if body.role and body.role != user.role:
        raise HTTPException(403, f"This account is registered as a {user.role}. Please use the {user.role.title()} login.")
    user.last_login_at = utcnow()
    db.commit()
    if settings.LOGIN_ALERT_EMAILS and user.email_notifications:
        subject, html_body = emailer.login_alert_email(
            user.full_name, user.last_login_at.strftime("%d %b %Y, %H:%M"),
            request.client.host if request.client else "unknown", request.headers.get("user-agent", "unknown"),
        )
        emailer.send_email(background_tasks, user.email, subject, html_body, kind="login_alert")
    return {"token": create_access_token(user), "user": user_payload(db, user)}


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return user_payload(db, user)


@router.patch("/me")
def update_me(body: ProfileIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(user, k, v)
    db.commit()
    return user_payload(db, user)


@router.post("/change-password")
def change_password(body: ChangePasswordIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}


@router.post("/forgot-password")
def forgot_password(body: ForgotIn, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user = db.query(User).filter(func.lower(User.email) == body.email.lower()).first()
    if user:
        user.reset_token = secrets.token_urlsafe(32)
        user.reset_token_expires = utcnow() + timedelta(minutes=60)
        db.commit()
        subject, html_body = emailer.reset_password_email(user.full_name, user.reset_token)
        emailer.send_email(background_tasks, user.email, subject, html_body, kind="password_reset")
    # Always succeed so the endpoint can't be used to discover registered emails.
    return {"ok": True, "message": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password")
def reset_password(body: ResetIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.reset_token == body.token).first()
    if user is None or not user.reset_token_expires or user.reset_token_expires < utcnow():
        raise HTTPException(400, "This reset link is invalid or has expired")
    user.password_hash = hash_password(body.password)
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()
    return {"ok": True}
