import hashlib
import json
from uuid import UUID

from flask import Blueprint, jsonify, request

from ..extensions import db
from ..models import Assessment, AssessmentAnswer, AssessmentMaterial, AssessmentSubmission
from ..services.ai_tasks import enqueue_ai_task
from ..services.assessment_service import generate_assessment_task, grade_submission_task, question_type_counts
from ..services.chunk_search import load_ready_materials
from ..services.language import normalize_language
from .errors import APIError


bp = Blueprint("assessments", __name__)


@bp.post("/assessments")
def create_assessment():
    payload = request.get_json(silent=True) or {}
    try:
        language = normalize_language(payload.get("language"))
    except ValueError as error:
        raise APIError(400, "INVALID_ARGUMENT", "language 必须是 zh-CN 或 en") from error
    request_id = _uuid(payload.get("request_id"), "request_id")
    material_ids = payload.get("material_ids")
    if not isinstance(material_ids, list) or not all(isinstance(item, str) for item in material_ids):
        raise APIError(400, "INVALID_ARGUMENT", "material_ids 必须是资料 ID 数组")
    material_ids = list(dict.fromkeys(material_ids))
    counts = payload.get("question_counts")
    if not isinstance(counts, dict) or set(counts) != {"single_choice", "true_false", "short_answer"}:
        raise APIError(400, "INVALID_ARGUMENT", "question_counts 结构无效")
    if not all(type(value) is int and value >= 0 for value in counts.values()):
        raise APIError(400, "INVALID_ARGUMENT", "各题型数量必须是非负整数")
    total = sum(counts.values())
    try:
        expected = question_type_counts(total)
    except ValueError as error:
        raise APIError(400, "INVALID_ARGUMENT", str(error)) from error
    if counts != expected:
        raise APIError(400, "INVALID_ARGUMENT", "题型配比必须约为 1/4 判断、1/2 选择，其余简答", expected)
    normalized = {"material_ids": material_ids, "question_counts": counts, "language": language}
    payload_hash = _payload_hash(normalized)
    existing = db.session.scalar(db.select(Assessment).where(Assessment.request_id == request_id))
    if existing:
        if existing.payload_hash != payload_hash:
            raise APIError(409, "IDEMPOTENCY_CONFLICT", "同一 request_id 已用于不同参数")
        return jsonify({"data": {"assessment_id": existing.id, "status": existing.status}}), 202

    materials = load_ready_materials(material_ids)
    assessment = Assessment(
        request_id=request_id,
        payload_hash=payload_hash,
        question_counts_json=counts,
        language=language,
    )
    db.session.add(assessment)
    db.session.flush()
    for material in materials:
        db.session.add(
            AssessmentMaterial(
                assessment_id=assessment.id,
                version_id=material.current_version.id,
                filename_snapshot=material.filename,
            )
        )
    db.session.commit()
    enqueue_ai_task(generate_assessment_task, assessment.id)
    return jsonify({"data": {"assessment_id": assessment.id, "status": assessment.status}}), 202


@bp.get("/assessments")
def list_assessments():
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except ValueError as error:
        raise APIError(400, "INVALID_ARGUMENT", "page 和 page_size 必须是整数") from error
    query = db.select(Assessment).order_by(Assessment.created_at.desc())
    status = request.args.get("status")
    if status:
        query = query.where(Assessment.status == status)
    pagination = db.paginate(query, page=page, per_page=page_size, error_out=False)
    return jsonify(
        {
            "data": {
                "items": [_assessment_history(item) for item in pagination.items],
                "total": pagination.total,
                "page": page,
                "page_size": page_size,
            }
        }
    )


@bp.get("/assessments/<assessment_id>")
def get_assessment(assessment_id: str):
    assessment = _assessment_or_404(assessment_id)
    return jsonify({"data": _assessment_detail(assessment)})


@bp.post("/assessments/<assessment_id>/submissions")
def submit_assessment(assessment_id: str):
    assessment = _assessment_or_404(assessment_id)
    payload = request.get_json(silent=True) or {}
    request_id = _uuid(payload.get("request_id"), "request_id")
    existing_request = db.session.scalar(
        db.select(AssessmentSubmission).where(AssessmentSubmission.request_id == request_id)
    )
    if existing_request:
        if existing_request.assessment_id != assessment.id:
            raise APIError(409, "IDEMPOTENCY_CONFLICT", "同一 request_id 已用于其他测评")
        return jsonify({"data": {"submission_id": existing_request.id, "status": existing_request.status}}), 202
    if assessment.submission:
        raise APIError(409, "ALREADY_SUBMITTED", "这份试卷已经提交")
    if assessment.status != "ready":
        raise APIError(409, "INVALID_STATUS", "只有已生成完成的试卷可以提交")
    answers = payload.get("answers")
    if not isinstance(answers, list):
        raise APIError(400, "INVALID_ARGUMENT", "answers 必须是数组")
    answer_map = {}
    question_by_id = {item.id: item for item in assessment.questions}
    for item in answers:
        if not isinstance(item, dict) or item.get("question_id") not in question_by_id:
            raise APIError(400, "INVALID_ARGUMENT", "answers 包含无效题目")
        question_id = item["question_id"]
        if question_id in answer_map:
            raise APIError(400, "INVALID_ARGUMENT", "同一道题不能重复作答")
        answer_map[question_id] = _validated_answer(question_by_id[question_id], item.get("answer"))
    unanswered = sum(_is_unanswered(answer_map.get(item.id)) for item in assessment.questions)
    if unanswered and payload.get("confirm_unanswered") is not True:
        raise APIError(422, "UNANSWERED_QUESTIONS", "仍有题目未作答", {"unanswered_count": unanswered})
    duration = payload.get("duration_seconds")
    if type(duration) is not int or duration < 0:
        raise APIError(400, "INVALID_ARGUMENT", "duration_seconds 必须是非负整数")
    submission = AssessmentSubmission(
        assessment_id=assessment.id,
        request_id=request_id,
        status="grading",
        max_score=len(assessment.questions),
        duration_seconds=duration,
    )
    db.session.add(submission)
    db.session.flush()
    for question in assessment.questions:
        db.session.add(
            AssessmentAnswer(
                submission_id=submission.id,
                question_id=question.id,
                answer_json=answer_map.get(question.id),
            )
        )
    assessment.status = "grading"
    db.session.commit()
    enqueue_ai_task(grade_submission_task, submission.id)
    return jsonify({"data": {"submission_id": submission.id, "status": submission.status}}), 202


@bp.get("/assessments/<assessment_id>/result")
def get_result(assessment_id: str):
    assessment = _assessment_or_404(assessment_id)
    return jsonify({"data": _result_data(assessment)})


@bp.post("/assessments/<assessment_id>/retry-grading")
def retry_grading(assessment_id: str):
    assessment = _assessment_or_404(assessment_id)
    submission = assessment.submission
    if assessment.status != "grading_failed" or submission is None:
        raise APIError(409, "INVALID_STATUS", "只有评分失败的测评可以重试")
    submission.status = "grading"
    submission.error_message = None
    assessment.status = "grading"
    assessment.error_message = None
    db.session.commit()
    enqueue_ai_task(grade_submission_task, submission.id)
    return jsonify({"data": {"submission_id": submission.id, "status": submission.status}}), 202


def _assessment_or_404(assessment_id: str) -> Assessment:
    assessment = db.session.get(Assessment, assessment_id)
    if assessment is None:
        raise APIError(404, "NOT_FOUND", "测评不存在")
    return assessment


def _material_scope(assessment: Assessment) -> list[dict]:
    return [
        {
            "material_id": item.version.material_id,
            "version_id": item.version_id,
            "filename": item.filename_snapshot,
        }
        for item in assessment.materials
    ]


def _assessment_detail(assessment: Assessment) -> dict:
    data = {
        "id": assessment.id,
        "status": assessment.status,
        "material_scope": _material_scope(assessment),
        "question_counts": assessment.question_counts_json,
        "language": assessment.language,
        "questions": [],
        "error_message": assessment.error_message,
        "created_at": assessment.created_at.isoformat(),
    }
    if assessment.status in {"ready", "grading", "graded", "grading_failed"}:
        data["questions"] = [
            {
                "id": item.id,
                "order_no": item.order_no,
                "type": item.type,
                "difficulty": item.difficulty,
                "stem": item.stem,
                "options": _public_options(item),
                "max_score": float(item.max_score),
            }
            for item in assessment.questions
        ]
    return data


def _assessment_history(assessment: Assessment) -> dict:
    submission = assessment.submission
    total_score = float(submission.total_score) if submission and submission.total_score is not None else None
    max_score = float(submission.max_score) if submission else None
    return {
        "id": assessment.id,
        "created_at": assessment.created_at.isoformat(),
        "submitted_at": submission.submitted_at.isoformat() if submission else None,
        "graded_at": submission.graded_at.isoformat() if submission and submission.graded_at else None,
        "duration_seconds": submission.duration_seconds if submission else None,
        "material_scope": _material_scope(assessment),
        "status": assessment.status,
        "language": assessment.language,
        "total_score": total_score,
        "max_score": max_score,
        "score_rate": round(total_score / max_score * 100, 1) if total_score is not None and max_score else None,
    }


def _result_data(assessment: Assessment) -> dict:
    submission = assessment.submission
    base = {
        "status": assessment.status,
        "language": assessment.language,
        "total_score": None,
        "max_score": float(submission.max_score) if submission else len(assessment.questions),
        "score_rate": None,
        "duration_seconds": submission.duration_seconds if submission else None,
        "material_scope": _material_scope(assessment),
        "graded_at": submission.graded_at.isoformat() if submission and submission.graded_at else None,
        "error_message": (submission.error_message if submission else assessment.error_message),
        "questions": [],
    }
    if not submission or assessment.status != "graded":
        return base
    answer_by_question = {item.question_id: item for item in submission.answers}
    total_score = float(submission.total_score)
    max_score = float(submission.max_score)
    base.update(total_score=total_score, score_rate=round(total_score / max_score * 100, 1))
    for question in assessment.questions:
        answer = answer_by_question[question.id]
        base["questions"].append(
            {
                "id": question.id,
                "order_no": question.order_no,
                "type": question.type,
                "difficulty": question.difficulty,
                "stem": question.stem,
                "options": _public_options(question),
                "answer": answer.answer_json,
                "correct_answer": question.correct_answer_json,
                "score": float(answer.score),
                "feedback": answer.feedback,
                "matched_points": answer.matched_points_json or [],
                "missing_points": answer.missing_points_json or [],
                "knowledge_point": {
                    "id": question.knowledge_point_id,
                    "name": question.knowledge_point_name_snapshot,
                },
                "sources": question.sources_snapshot_json or [],
            }
        )
    return base


def _validated_answer(question, answer):
    if answer is None:
        return None
    if question.type == "single_choice":
        if answer not in {"A", "B", "C", "D"}:
            raise APIError(400, "INVALID_ARGUMENT", "单选题答案必须是 A、B、C 或 D")
    elif question.type == "true_false":
        if type(answer) is not bool:
            raise APIError(400, "INVALID_ARGUMENT", "判断题答案必须是布尔值")
    elif not isinstance(answer, str):
        raise APIError(400, "INVALID_ARGUMENT", "简答题答案必须是字符串")
    return answer.strip() if isinstance(answer, str) else answer


def _is_unanswered(answer) -> bool:
    return answer is None or answer == ""


def _uuid(value, name: str) -> str:
    if not isinstance(value, str):
        raise APIError(400, "INVALID_ARGUMENT", f"{name} 必须是 UUID")
    try:
        return str(UUID(value))
    except ValueError as error:
        raise APIError(400, "INVALID_ARGUMENT", f"{name} 必须是 UUID") from error


def _payload_hash(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _public_options(question) -> dict[str, str]:
    if question.type == "true_false":
        return (
            {"A": "True", "B": "False"}
            if question.assessment.language == "en"
            else {"A": "正确", "B": "错误"}
        )
    if question.type == "short_answer":
        return {}
    value = question.options_json
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    if isinstance(value, list):
        return {
            item["id"]: item["text"]
            for item in value
            if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("text"), str)
        }
    if isinstance(value, dict):
        return {str(key): text for key, text in value.items() if isinstance(text, str)}
    return {}
