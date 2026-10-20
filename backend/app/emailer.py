"""Email delivery + branded templates.

When SMTP is configured, mail is sent via SMTP. Otherwise every message is written to
backend/outbox/*.html and recorded in the email_logs table so it can be viewed from the
admin "Email Outbox" page during development.
"""
import html
import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from .config import BASE_DIR, settings
from .database import SessionLocal
from .models import EmailLog, utcnow

log = logging.getLogger("edupulse.email")
OUTBOX = BASE_DIR / "outbox"


def _deliver(to_email: str, subject: str, html_body: str, kind: str) -> None:
    db = SessionLocal()
    entry = EmailLog(to_email=to_email, subject=subject, body=html_body, kind=kind, status="queued")
    db.add(entry)
    db.commit()
    try:
        if settings.smtp_enabled:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = settings.SMTP_FROM
            msg["To"] = to_email
            msg.set_content(re.sub(r"<[^>]+>", "", html_body))
            msg.add_alternative(html_body, subtype="html")
            if settings.SMTP_PORT == 465:
                with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, context=ssl.create_default_context(), timeout=20) as s:
                    if settings.SMTP_USER:
                        s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as s:
                    if settings.SMTP_USE_TLS:
                        s.starttls(context=ssl.create_default_context())
                    if settings.SMTP_USER:
                        s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                    s.send_message(msg)
            entry.status = "sent"
        else:
            OUTBOX.mkdir(exist_ok=True)
            safe = re.sub(r"[^a-zA-Z0-9]+", "_", f"{to_email}_{subject}")[:80]
            (OUTBOX / f"{utcnow():%Y%m%d_%H%M%S}_{entry.id}_{safe}.html").write_text(html_body, encoding="utf-8")
            entry.status = "logged"
            log.info("Email (dev outbox) -> %s | %s", to_email, subject)
    except Exception as exc:  # noqa: BLE001 - we never want email failures to break requests
        entry.status = "failed"
        entry.error = str(exc)
        log.exception("Failed to send email to %s", to_email)
    finally:
        db.commit()
        db.close()


def send_email(background_tasks, to_email: str | None, subject: str, html_body: str, kind: str = "general") -> None:
    """Queue an email to be delivered after the response is returned."""
    if not to_email:
        return
    if background_tasks is None:
        _deliver(to_email, subject, html_body, kind)
    else:
        background_tasks.add_task(_deliver, to_email, subject, html_body, kind)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def _layout(title: str, body: str, cta_label: str | None = None, cta_url: str | None = None) -> str:
    cta = ""
    if cta_label and cta_url:
        cta = (
            f'<p style="margin:28px 0 8px"><a href="{html.escape(cta_url)}" '
            'style="background:linear-gradient(135deg,#6366f1,#06b6d4);color:#fff;text-decoration:none;'
            'padding:12px 22px;border-radius:10px;font-weight:600;display:inline-block">'
            f"{html.escape(cta_label)}</a></p>"
        )
    return f"""<!doctype html>
<html><body style="margin:0;background:#f1f5f9;font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#0f172a">
<table width="100%" cellpadding="0" cellspacing="0" style="padding:32px 12px"><tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(15,23,42,.08)">
<tr><td style="background:linear-gradient(135deg,#312e81,#4f46e5 55%,#0891b2);padding:28px 32px;color:#fff">
<div style="font-size:13px;letter-spacing:.14em;text-transform:uppercase;opacity:.8">EduPulse AI</div>
<div style="font-size:22px;font-weight:700;margin-top:6px">{html.escape(title)}</div></td></tr>
<tr><td style="padding:28px 32px;font-size:15px;line-height:1.6">{body}{cta}</td></tr>
<tr><td style="padding:18px 32px;background:#f8fafc;color:#64748b;font-size:12px">
You received this email because you have an EduPulse AI account. You can turn off non-essential notifications in Settings.
</td></tr></table></td></tr></table></body></html>"""


ROLE_BLURBS = {
    "admin": "As an <b>Administrator</b>, you can manage students, teachers, classes, subjects, exams, assignments and grading systems, and run AI early-warning analysis for the whole institution.",
    "teacher": "As a <b>Teacher</b>, you can record marks, attendance, homework and remarks, and use AI insights and the AI Teacher Assistant to plan interventions.",
    "student": "As a <b>Student</b>, you can track your performance and attendance, discover your weak subjects and topics, and follow a personalised AI study plan.",
    "parent": "As a <b>Parent</b>, you can monitor your child's progress, attendance and homework, and receive early-warning alerts by email.",
}


def welcome_email(name: str, role: str, temp_password: str | None = None) -> tuple[str, str]:
    extra = ""
    if temp_password:
        extra = (
            f"<p>An account was created for you. Your temporary password is "
            f'<code style="background:#eef2ff;padding:2px 8px;border-radius:6px">{html.escape(temp_password)}</code>. '
            "Please change it after signing in.</p>"
        )
    body = (
        f"<p>Hi {html.escape(name)},</p>"
        "<p>Welcome to <b>EduPulse AI</b> &mdash; the student performance intelligence platform that turns "
        "everyday school data into early insights and personalised action plans.</p>"
        f"<p>{ROLE_BLURBS.get(role, '')}</p>{extra}"
    )
    return "Welcome to EduPulse AI 🎓", _layout("Welcome aboard!", body, "Open your dashboard", f"{settings.FRONTEND_URL}/login?role={role}")


def login_alert_email(name: str, when: str, ip: str, agent: str) -> tuple[str, str]:
    body = (
        f"<p>Hi {html.escape(name)},</p><p>We noticed a new sign-in to your EduPulse AI account.</p>"
        '<table style="font-size:14px;color:#334155">'
        f"<tr><td style='padding:2px 12px 2px 0'><b>Time</b></td><td>{html.escape(when)} UTC</td></tr>"
        f"<tr><td style='padding:2px 12px 2px 0'><b>IP address</b></td><td>{html.escape(ip)}</td></tr>"
        f"<tr><td style='padding:2px 12px 2px 0'><b>Device</b></td><td>{html.escape(agent[:120])}</td></tr></table>"
        "<p>If this was you, no action is needed. If not, reset your password immediately.</p>"
    )
    return "New sign-in to your EduPulse AI account", _layout("New sign-in detected", body, "Reset password", f"{settings.FRONTEND_URL}/forgot-password")


def reset_password_email(name: str, token: str) -> tuple[str, str]:
    link = f"{settings.FRONTEND_URL}/reset-password?token={token}"
    body = (
        f"<p>Hi {html.escape(name)},</p><p>We received a request to reset your password. "
        "This link is valid for 60 minutes. If you didn't request it, you can ignore this email.</p>"
    )
    return "Reset your EduPulse AI password", _layout("Password reset", body, "Choose a new password", link)


def risk_alert_email(recipient_name: str, student_name: str, title: str, message: str, reasons: list[str]) -> tuple[str, str]:
    items = "".join(f"<li>{html.escape(r)}</li>" for r in reasons)
    body = (
        f"<p>Hi {html.escape(recipient_name)},</p>"
        f"<p>EduPulse AI's early-warning system flagged an update for <b>{html.escape(student_name)}</b>.</p>"
        f'<div style="border-left:4px solid #f59e0b;background:#fffbeb;padding:12px 16px;border-radius:8px">'
        f"<b>{html.escape(title)}</b><br>{html.escape(message)}</div>"
        + (f"<p><b>Key factors</b></p><ul>{items}</ul>" if items else "")
        + "<p>Open EduPulse AI to view the detailed analysis and the recommended study plan.</p>"
    )
    return f"EduPulse alert: {title}", _layout("Early-warning alert", body, "View progress", f"{settings.FRONTEND_URL}/login")
