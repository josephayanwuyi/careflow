"""In-app notifications, SMS, and email alerting.

`notify()` adds a row to the current session so it commits with the change that caused it.
When patient phone or email is available, external channels (SMS and/or Email) are dispatched.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, func, or_, select

from careflow.errors import NotFound
from careflow.extensions import db
from careflow.models import Notification
from careflow.models.base import utcnow
from careflow.models.people import PatientProfile
from careflow.models.user import User
from careflow.security.policy import Actor
from careflow.services import email_service, sms_service


def notify(*, user_id: uuid.UUID | None, hospital_id: uuid.UUID | None, type: str, title: str, body: str,
           related_type: str | None = None, related_id: uuid.UUID | None = None,
           phone: str | None = None, email: str | None = None,
           sms: bool = True, send_email: bool = True) -> Notification | None:
    target_phone = phone
    target_email = email

    # 1. SMS delivery (e.g. delivered on first call)
    if sms:
        if not target_phone and user_id is not None:
            target_phone = db.session.scalar(select(PatientProfile.phone).where(PatientProfile.user_id == user_id))
        if target_phone:
            sms_text = f"CareFlow: {title}. {body}"
            sms_service.send_sms(to_phone=target_phone, message=sms_text)

    # 2. Email delivery (delivered on first call and every recall)
    if send_email:
        if not target_email and user_id is not None:
            target_email = db.session.scalar(select(User.email).where(User.id == user_id))
        if target_email:
            email_subject = f"CareFlow Update: {title}"
            email_body = f"Hello,\n\n{body}\n\nPlease check your CareFlow live queue status for updates.\n\nCareFlow Healthcare System"
            email_service.send_email(to_email=target_email, subject=email_subject, body=email_body)

    # 3. Track notification stats on hospital
    if hospital_id:
        from careflow.models.hospital import Hospital
        hosp = db.session.get(Hospital, hospital_id)
        if hosp:
            settings = dict(hosp.settings or {})
            stats = dict(settings.get("notification_stats") or {"sms_sent": 0, "email_sent": 0, "push_sent": 0})
            if sms and target_phone:
                stats["sms_sent"] = stats.get("sms_sent", 0) + 1
            if send_email and target_email:
                stats["email_sent"] = stats.get("email_sent", 0) + 1
            if user_id is not None:
                stats["push_sent"] = stats.get("push_sent", 0) + 1
            settings["notification_stats"] = stats
            hosp.settings = settings
            db.session.add(hosp)

    # 4. In-App Notification (and real-time event stream)
    if user_id is None:  # e.g. walk-in patients without an online account
        return None
    n = Notification(user_id=user_id, hospital_id=hospital_id, type=type, title=title[:120], body=body[:500],
                     related_type=related_type, related_id=related_id)
    db.session.add(n)
    return n


def get_notification_report(hospital_id: uuid.UUID) -> dict:
    from careflow.models.hospital import Hospital
    hospital = db.session.get(Hospital, hospital_id)
    if hospital is None:
        raise NotFound("Hospital not found.")
    stats = dict((hospital.settings or {}).get("notification_stats") or {"sms_sent": 0, "email_sent": 0, "push_sent": 0})

    recent = list(db.session.scalars(
        select(Notification)
        .where(Notification.hospital_id == hospital_id)
        .order_by(Notification.created_at.desc())
        .limit(50)
    ))

    items = []
    for n in recent:
        is_first_call = n.type == "patient.called" and "Room" in n.body
        is_recall = n.type == "patient.called" and "Recall" in n.title
        channel = "SMS + Email + Push" if is_first_call else ("Email + Push" if is_recall else "Push Notification")
        items.append({
            "id": str(n.id),
            "timestamp": n.created_at.isoformat() if n.created_at else "",
            "type": n.type,
            "title": n.title,
            "body": n.body,
            "channel": channel,
        })

    push_total = db.session.scalar(
        select(func.count()).select_from(Notification).where(Notification.hospital_id == hospital_id)
    ) or stats.get("push_sent", 0)

    return {
        "sms_sent": stats.get("sms_sent", 0),
        "email_sent": stats.get("email_sent", 0),
        "push_sent": push_total,
        "items": items,
    }


def list_for_user(actor: Actor, *, unread_only: bool, limit: int,
                  cursor: tuple[datetime, uuid.UUID] | None) -> list[Notification]:
    stmt = select(Notification).where(Notification.user_id == actor.user_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    if cursor:
        ts, last_id = cursor
        stmt = stmt.where(or_(Notification.created_at < ts, and_(Notification.created_at == ts, Notification.id < last_id)))
    return list(db.session.scalars(stmt.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit)))


def unread_count(actor: Actor) -> int:
    return db.session.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == actor.user_id, Notification.read_at.is_(None)))


def mark_read(actor: Actor, notification_id: uuid.UUID) -> Notification:
    n = db.session.get(Notification, notification_id)
    if n is None or n.user_id != actor.user_id:
        raise NotFound("Notification not found.")
    if n.read_at is None:
        n.read_at = utcnow()
        db.session.commit()
    return n


def mark_all_read(actor: Actor) -> int:
    rows = db.session.scalars(select(Notification).where(
        Notification.user_id == actor.user_id, Notification.read_at.is_(None))).all()
    now = utcnow()
    for n in rows:
        n.read_at = now
    db.session.commit()
    return len(rows)
