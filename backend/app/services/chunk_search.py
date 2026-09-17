import re

from sqlalchemy.orm import selectinload

from ..api.errors import APIError
from ..extensions import db
from ..models import Material, MaterialVersion


def _terms(text: str) -> set[str]:
    words = {item.casefold() for item in re.findall(r"[A-Za-z0-9_]{2,}", text)}
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        for width in (2, 3, 4):
            words.update(run[index : index + width] for index in range(max(0, len(run) - width + 1)))
    return words


def load_ready_materials(material_ids: list[str]) -> list[Material]:
    ordered = list(dict.fromkeys(material_ids))
    if not ordered:
        raise APIError(400, "INVALID_ARGUMENT", "至少选择一份资料")
    query = (
        db.select(Material)
        .where(Material.id.in_(ordered))
        .options(selectinload(Material.current_version).selectinload(MaterialVersion.chunks))
    )
    found = {item.id: item for item in db.session.scalars(query).all()}
    missing = [item_id for item_id in ordered if item_id not in found]
    if missing:
        raise APIError(404, "NOT_FOUND", "部分资料不存在", {"material_ids": missing})
    unavailable = [
        item_id
        for item_id in ordered
        if not found[item_id].current_version or found[item_id].current_version.status != "ready"
    ]
    if unavailable:
        raise APIError(409, "MATERIAL_NOT_READY", "部分资料尚未解析完成", {"material_ids": unavailable})
    return [found[item_id] for item_id in ordered]


def search_relevant_chunks(material_ids: list[str], query: str, limit: int = 12) -> tuple[list[dict], bool]:
    materials = load_ready_materials(material_ids)
    query_terms = _terms(query)
    candidates = []
    for material_order, material in enumerate(materials):
        version = material.current_version
        for chunk in version.chunks:
            chunk_terms = _terms(chunk.text)
            overlap = query_terms & chunk_terms
            weighted = sum(2 if len(term) >= 3 else 1 for term in overlap)
            candidates.append(
                (
                    weighted,
                    material_order,
                    chunk.chunk_index,
                    {
                        "source_id": chunk.id,
                        "material_id": material.id,
                        "version_id": version.id,
                        "filename": material.filename,
                        "declared_language": version.language,
                        "locator": chunk.locator_json or {},
                        "text": chunk.text,
                    },
                )
            )
    matched = any(score > 0 for score, *_ in candidates)
    selected = [item for item in candidates if item[0] > 0] if matched else candidates
    selected.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [item[3] for item in selected[:limit]], matched


def complete_assessment_context(material_ids: list[str]) -> tuple[list[dict], dict[str, dict]]:
    materials = load_ready_materials(material_ids)
    documents = []
    sources = {}
    for material in materials:
        version = material.current_version
        points_by_chunk = {}
        for point in version.knowledge_points:
            for link in point.sources:
                points_by_chunk.setdefault(link.chunk_id, point)
        chunks = []
        for chunk in version.chunks:
            point = points_by_chunk.get(chunk.id)
            if point is None:
                continue
            source = {
                "source_id": chunk.id,
                "locator": chunk.locator_json or {},
                "text": chunk.text,
                "knowledge_point_id": point.id,
                "knowledge_point_name": point.name,
                "knowledge_point_description": point.description,
            }
            chunks.append(source)
            sources[chunk.id] = source
        documents.append(
            {
                "material_id": material.id,
                "version_id": version.id,
                "filename": material.filename,
                "sources": chunks,
            }
        )
    return documents, sources
