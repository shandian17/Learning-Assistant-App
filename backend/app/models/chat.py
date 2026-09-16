from ..extensions import db
from .base import TimestampMixin, UUIDPrimaryKeyMixin, utc_now


message_role = db.Enum("user", "assistant", name="message_role", native_enum=False, create_constraint=True)
evidence_status = db.Enum(
    "sufficient", "partial", "insufficient", name="evidence_status", native_enum=False, create_constraint=True
)


class ChatSession(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "chat_sessions"

    materials = db.relationship("ChatSessionMaterial", back_populates="session")
    messages = db.relationship("ChatMessage", back_populates="session", order_by="ChatMessage.created_at")


class ChatSessionMaterial(db.Model):
    __tablename__ = "chat_session_materials"

    session_id = db.Column(db.String(36), db.ForeignKey("chat_sessions.id", ondelete="RESTRICT"), primary_key=True)
    material_id = db.Column(db.String(36), db.ForeignKey("materials.id", ondelete="RESTRICT"), primary_key=True)

    session = db.relationship("ChatSession", back_populates="materials")
    material = db.relationship("Material")


class ChatMessage(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "chat_messages"

    session_id = db.Column(db.String(36), db.ForeignKey("chat_sessions.id", ondelete="RESTRICT"), nullable=False)
    role = db.Column(message_role, nullable=False)
    content = db.Column(db.Text, nullable=False)
    evidence_status = db.Column(evidence_status, nullable=True)
    citations_json = db.Column(db.JSON, nullable=False, default=list)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    session = db.relationship("ChatSession", back_populates="messages")

    __table_args__ = (db.Index("ix_chat_messages_session_created", "session_id", "created_at"),)
