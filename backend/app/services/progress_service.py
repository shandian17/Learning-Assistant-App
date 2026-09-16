from ..extensions import db
from ..models import (
    AssessmentAnswer,
    AssessmentMaterial,
    AssessmentQuestion,
    AssessmentSubmission,
    KnowledgePoint,
    Material,
    MaterialVersion,
)


MASTERY_STATUSES = {"unassessed", "needs_review", "partial", "mastered"}


def current_knowledge_progress(material_ids: list[str] | None = None) -> list[dict]:
    query = (
        db.select(KnowledgePoint, Material)
        .join(MaterialVersion, KnowledgePoint.version_id == MaterialVersion.id)
        .join(Material, Material.current_version_id == MaterialVersion.id)
        .order_by(Material.created_at.asc(), KnowledgePoint.created_at.asc(), KnowledgePoint.id.asc())
    )
    if material_ids is not None:
        query = query.where(Material.id.in_(material_ids))
    rows = db.session.execute(query).all()
    point_ids = [point.id for point, _material in rows]
    latest = _latest_scores(point_ids)
    items = []
    for point, material in rows:
        score = latest.get(point.id)
        latest_score = score["latest_score"] if score else None
        items.append(
            {
                "knowledge_point_id": point.id,
                "name": point.name,
                "description": point.description,
                "material_id": material.id,
                "version_id": point.version_id,
                "filename": material.filename,
                "mastery_status": _mastery_status(latest_score),
                "latest_score": latest_score,
                "latest_assessment_id": score["latest_assessment_id"] if score else None,
                "assessed_at": score["assessed_at"] if score else None,
            }
        )
    return items


def graded_submissions(material_ids: list[str] | None = None) -> list[AssessmentSubmission]:
    query = (
        db.select(AssessmentSubmission)
        .where(AssessmentSubmission.status == "graded")
        .order_by(AssessmentSubmission.graded_at.desc(), AssessmentSubmission.id.desc())
    )
    if material_ids is not None:
        query = (
            query.join(
                AssessmentMaterial,
                AssessmentMaterial.assessment_id == AssessmentSubmission.assessment_id,
            )
            .join(MaterialVersion, MaterialVersion.id == AssessmentMaterial.version_id)
            .where(MaterialVersion.material_id.in_(material_ids))
            .distinct()
        )
    return list(db.session.scalars(query).unique().all())


def material_scope(submission: AssessmentSubmission) -> list[dict]:
    return [
        {
            "material_id": item.version.material_id,
            "version_id": item.version_id,
            "filename": item.filename_snapshot,
        }
        for item in submission.assessment.materials
    ]


def _latest_scores(point_ids: list[str]) -> dict[str, dict]:
    if not point_ids:
        return {}
    query = (
        db.select(
            AssessmentQuestion.knowledge_point_id,
            AssessmentQuestion.assessment_id,
            db.func.avg(AssessmentAnswer.score).label("average_score"),
            AssessmentSubmission.graded_at,
        )
        .join(AssessmentAnswer, AssessmentAnswer.question_id == AssessmentQuestion.id)
        .join(AssessmentSubmission, AssessmentSubmission.id == AssessmentAnswer.submission_id)
        .where(
            AssessmentQuestion.knowledge_point_id.in_(point_ids),
            AssessmentSubmission.status == "graded",
            AssessmentAnswer.score.is_not(None),
        )
        .group_by(
            AssessmentQuestion.knowledge_point_id,
            AssessmentQuestion.assessment_id,
            AssessmentSubmission.graded_at,
        )
        .order_by(AssessmentSubmission.graded_at.desc(), AssessmentQuestion.assessment_id.desc())
    )
    latest = {}
    for point_id, assessment_id, average_score, graded_at in db.session.execute(query):
        if point_id not in latest:
            latest[point_id] = {
                "latest_score": round(float(average_score), 3),
                "latest_assessment_id": assessment_id,
                "assessed_at": graded_at.isoformat(),
            }
    return latest


def _mastery_status(score: float | None) -> str:
    if score is None:
        return "unassessed"
    if score == 0:
        return "needs_review"
    if score == 1:
        return "mastered"
    return "partial"
