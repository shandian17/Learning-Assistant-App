from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from ..extensions import db
from ..models import Material, MaterialNote
from ..models.base import utc_now
from ..services.progress_service import (
    MASTERY_STATUSES,
    current_knowledge_progress,
    graded_submissions,
    material_scope,
)
from .errors import APIError


bp = Blueprint("progress", __name__)


@bp.get("/progress/summary")
def summary():
    material_ids = _material_ids()
    materials = _materials(material_ids)
    points = current_knowledge_progress(material_ids)
    submissions = graded_submissions(material_ids)
    status_counts = {status: 0 for status in MASTERY_STATUSES}
    by_material = {material.id: [] for material in materials}
    for point in points:
        status_counts[point["mastery_status"]] += 1
        by_material.setdefault(point["material_id"], []).append(point)
    assessed_points = len(points) - status_counts["unassessed"]
    mastered_materials = sum(
        bool(material_points) and all(point["mastery_status"] == "mastered" for point in material_points)
        for material_points in by_material.values()
    )
    rates = [float(item.total_score / item.max_score * 100) for item in submissions]
    latest = submissions[0] if submissions else None
    return jsonify(
        {
            "data": {
                "total_materials": len(materials),
                "mastered_materials": mastered_materials,
                "assessments_count": len(submissions),
                "average_score_rate": round(sum(rates) / len(rates), 1) if rates else None,
                "weak_points_count": status_counts["partial"] + status_counts["needs_review"],
                "knowledge_points_total": len(points),
                "assessed_points": assessed_points,
                "coverage_rate": round(assessed_points / len(points) * 100, 1) if points else None,
                "mastery_counts": status_counts,
                "latest_score_rate": (
                    round(float(latest.total_score / latest.max_score * 100), 1) if latest else None
                ),
                "latest_assessment_id": latest.assessment_id if latest else None,
                "latest_graded_at": latest.graded_at.isoformat() if latest else None,
                "latest_material_scope": material_scope(latest) if latest else [],
            }
        }
    )


@bp.get("/progress/knowledge-points")
def knowledge_points():
    material_ids = _material_ids()
    status = request.args.get("mastery_status")
    if status and status not in MASTERY_STATUSES:
        raise APIError(400, "INVALID_ARGUMENT", "mastery_status 无效")
    points = current_knowledge_progress(material_ids)
    if status:
        points = [item for item in points if item["mastery_status"] == status]
    page, page_size = _pagination()
    return jsonify({"data": _page(points, page, page_size)})


@bp.get("/progress/weak-points")
def weak_points():
    material_ids = _material_ids()
    points = [
        item
        for item in current_knowledge_progress(material_ids)
        if item["mastery_status"] in {"partial", "needs_review"}
    ]
    page, page_size = _pagination()
    return jsonify({"data": _page(points, page, page_size)})


@bp.get("/progress/materials")
def material_progress():
    page, page_size = _pagination()
    pagination = db.paginate(
        db.select(Material).order_by(Material.created_at.asc()),
        page=page,
        per_page=page_size,
        error_out=False,
    )
    ids = [material.id for material in pagination.items]
    points_by_material = {material_id: [] for material_id in ids}
    for point in current_knowledge_progress(ids):
        points_by_material[point["material_id"]].append(point)
    items = []
    for material in pagination.items:
        points = points_by_material[material.id]
        total = len(points)
        mastered = sum(item["mastery_status"] == "mastered" for item in points)
        partial = sum(item["mastery_status"] == "partial" for item in points)
        needs_review = sum(item["mastery_status"] == "needs_review" for item in points)
        assessed = mastered + partial + needs_review
        note = material.note
        items.append(
            {
                "material_id": material.id,
                "filename": material.filename,
                "version_id": material.current_version_id,
                "knowledge_points_total": total,
                "assessed_points": assessed,
                "mastered_points": mastered,
                "partial_points": partial,
                "needs_review_points": needs_review,
                "coverage_rate": round(assessed / total * 100, 1) if total else None,
                "mastery_rate": round(mastered / total * 100, 1) if total else None,
                "note": {
                    "content": note.content if note else "",
                    "updated_at": note.updated_at.isoformat() if note else None,
                },
            }
        )
    return jsonify(
        {
            "data": {
                "items": items,
                "total": pagination.total,
                "page": page,
                "page_size": page_size,
            }
        }
    )


@bp.get("/progress/scores")
def score_history():
    material_ids = _material_ids()
    date_from = _timestamp(request.args.get("from"), "from")
    date_to = _timestamp(request.args.get("to"), "to")
    if date_from and date_to and date_from >= date_to:
        raise APIError(400, "INVALID_ARGUMENT", "from 必须早于 to")
    submissions = graded_submissions(material_ids)
    submissions.reverse()
    if date_from:
        submissions = [item for item in submissions if _as_utc(item.graded_at) >= date_from]
    if date_to:
        submissions = [item for item in submissions if _as_utc(item.graded_at) < date_to]
    items = [
        {
            "assessment_id": item.assessment_id,
            "graded_at": item.graded_at.isoformat(),
            "score_rate": round(float(item.total_score / item.max_score * 100), 1),
            "material_scope": material_scope(item),
        }
        for item in submissions
    ]
    page, page_size = _pagination()
    return jsonify({"data": _page(items, page, page_size)})


def save_material_note(material_id: str):
    material = db.session.get(Material, material_id)
    if material is None:
        raise APIError(404, "NOT_FOUND", "资料不存在")
    payload = request.get_json(silent=True) or {}
    content = payload.get("content")
    if not isinstance(content, str):
        raise APIError(400, "INVALID_ARGUMENT", "content 必须是字符串")
    if len(content) > 200_000:
        raise APIError(400, "INVALID_ARGUMENT", "笔记内容过长")
    note = db.session.get(MaterialNote, material_id)
    now = utc_now()
    if note is None:
        note = MaterialNote(material_id=material_id, content=content, updated_at=now)
        db.session.add(note)
    else:
        note.content = content
        note.updated_at = now
    db.session.commit()
    return jsonify(
        {
            "data": {
                "material_id": material_id,
                "content": note.content,
                "updated_at": note.updated_at.isoformat(),
            }
        }
    )


def _materials(material_ids: list[str] | None) -> list[Material]:
    query = db.select(Material).order_by(Material.created_at.asc())
    if material_ids is not None:
        query = query.where(Material.id.in_(material_ids))
    return list(db.session.scalars(query).all())


def _material_ids() -> list[str] | None:
    raw = request.args.get("material_ids")
    if raw is None:
        return None
    ids = list(dict.fromkeys(item.strip() for item in raw.split(",") if item.strip()))
    if not ids:
        raise APIError(400, "INVALID_ARGUMENT", "material_ids 不能为空")
    existing = set(db.session.scalars(db.select(Material.id).where(Material.id.in_(ids))).all())
    missing = [item for item in ids if item not in existing]
    if missing:
        raise APIError(404, "NOT_FOUND", "部分资料不存在", {"material_ids": missing})
    return ids


def _pagination() -> tuple[int, int]:
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except ValueError as error:
        raise APIError(400, "INVALID_ARGUMENT", "page 和 page_size 必须是整数") from error
    return page, page_size


def _page(items: list[dict], page: int, page_size: int) -> dict:
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": len(items),
        "page": page,
        "page_size": page_size,
    }


def _timestamp(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise APIError(400, "INVALID_ARGUMENT", f"{field} 必须是 ISO 8601 时间") from error
    if parsed.tzinfo is None:
        raise APIError(400, "INVALID_ARGUMENT", f"{field} 必须包含时区")
    return parsed.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
