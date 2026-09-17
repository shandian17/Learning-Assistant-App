from ..extensions import db
from .base import TimestampMixin, UUIDPrimaryKeyMixin, utc_now


report_period_type = db.Enum("week", "custom", name="report_period_type", native_enum=False, create_constraint=True)
report_status = db.Enum(
    "generating", "ready", "generation_failed", name="report_status", native_enum=False, create_constraint=True
)


class WeeklyReport(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "weekly_reports"

    period_type = db.Column(report_period_type, nullable=False)
    date_from = db.Column(db.Date, nullable=False)
    date_to = db.Column(db.Date, nullable=False)
    timezone = db.Column(db.String(128), nullable=False)
    language = db.Column(db.String(8), nullable=False, default="zh-CN", server_default="zh-CN")
    status = db.Column(report_status, nullable=False, default="generating")
    error_message = db.Column(db.Text, nullable=True)
    data_cutoff_at = db.Column(db.DateTime(timezone=True), nullable=True)
    statistics_json = db.Column(db.JSON, nullable=True)
    content_json = db.Column(db.JSON, nullable=True)
    generated_at = db.Column(db.DateTime(timezone=True), nullable=True)

    generation_requests = db.relationship("ReportGenerationRequest", back_populates="report")

    __table_args__ = (
        db.UniqueConstraint("date_from", "date_to", "timezone", name="uq_report_period_timezone"),
        db.CheckConstraint("date_from <= date_to", name="ck_report_date_range"),
        db.Index("ix_weekly_reports_generated_at", "generated_at"),
    )


class ReportGenerationRequest(db.Model):
    __tablename__ = "report_generation_requests"

    request_id = db.Column(db.String(36), primary_key=True)
    report_id = db.Column(db.String(36), db.ForeignKey("weekly_reports.id", ondelete="RESTRICT"), nullable=False)
    payload_hash = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    report = db.relationship("WeeklyReport", back_populates="generation_requests")
