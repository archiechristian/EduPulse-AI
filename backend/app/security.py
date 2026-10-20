from datetime import timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import Student, Teacher, User, utcnow

_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "exp": utcnow() + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, settings.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not found or disabled")
    return user


def require_roles(*roles: str):
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this resource")
        return user

    return checker


def teacher_for(db: Session, user: User) -> Teacher | None:
    return db.query(Teacher).filter(Teacher.user_id == user.id).first()


def accessible_student_ids(db: Session, user: User) -> set[int] | None:
    """None means unrestricted (admin/teacher)."""
    if user.role in ("admin", "teacher"):
        return None
    if user.role == "student":
        return {s.id for s in db.query(Student.id).filter(Student.user_id == user.id)}
    if user.role == "parent":
        return {s.id for s in db.query(Student.id).filter(Student.parent_user_id == user.id)}
    return set()


def ensure_student_access(db: Session, user: User, student_id: int) -> Student:
    student = db.get(Student, student_id)
    if student is None:
        raise HTTPException(404, "Student not found")
    allowed = accessible_student_ids(db, user)
    if allowed is not None and student_id not in allowed:
        raise HTTPException(403, "You do not have access to this student")
    return student
