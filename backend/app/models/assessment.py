from decimal import Decimal

from ..extensions import db
from .base import CreatedAtMixin, UUIDPrimaryKeyMixin, new_uuid, utc_now


assessment_status = db.Enum(
    "generating",
    "ready",
    "grading",
    "graded",
    "generation_failed",
    "grading_failed",
    name="assessment_status",
    native_enum=False,
    create_constraint=True,
)
question_type = db.Enum(
    "single_choice", "true_false", "short_answer", name="question_type", native_enum=False, create_constraint=True
)
question_difficulty = db.Enum(
    "easy", "medium", "hard", name="question_difficulty", native_enum=False, create_constraint=True
)
submission_status = db.Enum(
    "grading", "graded", "grading_failed", name="submission_status", native_enum=False, create_constraint=True
)


class Assessment(UUIDPrimaryKeyMixin, CreatedAtMixin, db.Model):
    __tablename__ = "assessments"

    request_id = db.Column(db.String(36), nullable=False, unique=True, default=new_uuid)
    payload_hash = db.Column(db.String(64), nullable=False, default="")
    language = db.Column(db.String(8), nullable=False, default="zh-CN", server_default="zh-CN")
    status = db.Column(assessment_status, nullable=False, default="generating")
    question_counts_json = db.Column(db.JSON, nullable=False, default=dict)
    error_message = db.Column(db.Text, nullable=True)

    materials = db.relationship("AssessmentMaterial", back_populates="assessment")
    questions = db.relationship("AssessmentQuestion", back_populates="assessment", order_by="AssessmentQuestion.order_no")
    submission = db.relationship("AssessmentSubmission", back_populates="assessment", uselist=False)

    __table_args__ = (db.Index("ix_assessments_created_at", "created_at"),)


class AssessmentMaterial(db.Model):
    __tablename__ = "assessment_materials"

    assessment_id = db.Column(db.String(36), db.ForeignKey("assessments.id", ondelete="RESTRICT"), primary_key=True)
    version_id = db.Column(db.String(36), db.ForeignKey("material_versions.id", ondelete="RESTRICT"), primary_key=True)
    filename_snapshot = db.Column(db.String(512), nullable=False)

    assessment = db.relationship("Assessment", back_populates="materials")
    version = db.relationship("MaterialVersion")


class AssessmentQuestion(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "assessment_questions"

    assessment_id = db.Column(db.String(36), db.ForeignKey("assessments.id", ondelete="RESTRICT"), nullable=False)
    order_no = db.Column(db.Integer, nullable=False)
    type = db.Column(question_type, nullable=False)
    difficulty = db.Column(question_difficulty, nullable=False)
    stem = db.Column(db.Text, nullable=False)
    options_json = db.Column(db.JSON, nullable=True)
    correct_answer_json = db.Column(db.JSON, nullable=False)
    rubric_json = db.Column(db.JSON, nullable=True)
    max_score = db.Column(db.Numeric(3, 1), nullable=False, default=Decimal("1.0"))
    knowledge_point_id = db.Column(
        db.String(36), db.ForeignKey("knowledge_points.id", ondelete="RESTRICT"), nullable=False
    )
    knowledge_point_name_snapshot = db.Column(db.String(512), nullable=False)
    sources_snapshot_json = db.Column(db.JSON, nullable=False, default=list)

    assessment = db.relationship("Assessment", back_populates="questions")
    knowledge_point = db.relationship("KnowledgePoint")
    answers = db.relationship("AssessmentAnswer", back_populates="question")

    __table_args__ = (
        db.UniqueConstraint("assessment_id", "order_no", name="uq_assessment_question_order"),
        db.CheckConstraint("order_no > 0", name="ck_assessment_question_order"),
        db.CheckConstraint("max_score = 1", name="ck_assessment_question_max_score"),
        db.Index("ix_assessment_questions_knowledge_point", "knowledge_point_id"),
    )


class AssessmentSubmission(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "assessment_submissions"

    assessment_id = db.Column(
        db.String(36), db.ForeignKey("assessments.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    request_id = db.Column(db.String(36), nullable=False, unique=True)
    status = db.Column(submission_status, nullable=False, default="grading")
    total_score = db.Column(db.Numeric(6, 1), nullable=True)
    max_score = db.Column(db.Numeric(6, 1), nullable=False)
    duration_seconds = db.Column(db.Integer, nullable=False)
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    graded_at = db.Column(db.DateTime(timezone=True), nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    assessment = db.relationship("Assessment", back_populates="submission")
    answers = db.relationship("AssessmentAnswer", back_populates="submission")

    __table_args__ = (
        db.CheckConstraint("total_score IS NULL OR total_score >= 0", name="ck_submission_total_score"),
        db.CheckConstraint("max_score > 0", name="ck_submission_max_score"),
        db.CheckConstraint("duration_seconds >= 0", name="ck_submission_duration"),
        db.Index("ix_assessment_submissions_graded_at", "graded_at"),
    )


class AssessmentAnswer(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "assessment_answers"

    submission_id = db.Column(
        db.String(36), db.ForeignKey("assessment_submissions.id", ondelete="RESTRICT"), nullable=False
    )
    question_id = db.Column(
        db.String(36), db.ForeignKey("assessment_questions.id", ondelete="RESTRICT"), nullable=False
    )
    answer_json = db.Column(db.JSON, nullable=True)
    score = db.Column(db.Numeric(3, 1), nullable=True)
    feedback = db.Column(db.Text, nullable=True)
    matched_points_json = db.Column(db.JSON, nullable=False, default=list)
    missing_points_json = db.Column(db.JSON, nullable=False, default=list)

    submission = db.relationship("AssessmentSubmission", back_populates="answers")
    question = db.relationship("AssessmentQuestion", back_populates="answers")

    __table_args__ = (
        db.UniqueConstraint("submission_id", "question_id", name="uq_submission_question_answer"),
        db.CheckConstraint("score IS NULL OR score IN (0, 0.5, 1)", name="ck_assessment_answer_score"),
    )
