"""Persistent internal event outbox and webhook deduplication."""

from __future__ import annotations

import json
import uuid

from sqlalchemy.exc import IntegrityError

from models import EventInbox, EventOutbox, db


def publish(event_type: str, subject: str, correlation_id: str, payload: dict) -> EventOutbox:
    event = EventOutbox(
        id=str(uuid.uuid4()), event_type=event_type, subject=subject,
        correlation_id=correlation_id, payload_json=json.dumps(payload, sort_keys=True),
    )
    db.session.add(event)
    return event


def accept_webhook(source: str, external_event_id: str) -> bool:
    if not source or not external_event_id or len(external_event_id) > 255:
        raise ValueError("Invalid webhook event identifier")
    db.session.add(EventInbox(source=source, external_event_id=external_event_id))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


def mark_delivered(event: EventOutbox) -> None:
    event.status = "delivered"
    event.attempts += 1
    db.session.commit()


def mark_failed(event: EventOutbox, max_attempts: int = 3) -> None:
    event.attempts += 1
    event.status = "dead_letter" if event.attempts >= max_attempts else "pending"
    db.session.commit()
