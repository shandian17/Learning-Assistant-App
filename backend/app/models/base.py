from datetime import datetime, timezone
from uuid import uuid4

from ..extensions import db


def new_uuid() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UUIDPrimaryKeyMixin:
    id = db.Column(db.String(36), primary_key=True, default=new_uuid)


class CreatedAtMixin:
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class TimestampMixin(CreatedAtMixin):
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
