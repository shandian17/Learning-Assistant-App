import json

from sqlalchemy.orm import selectinload

from ..api.errors import APIError
from ..extensions import db
from ..models import Material, MaterialVersion


def build_document_context(material_ids: list[str]) -> str:
    """Load every parsed text block from the selected current document versions.

    This deliberately performs no similarity search, ranking, top-k selection, or
    vector lookup. The complete parsed content of each selected document is
    returned in document order for direct inclusion in an LLM request.
    """
    ordered_ids = list(dict.fromkeys(material_ids))
    if not ordered_ids:
        raise APIError(400, "INVALID_ARGUMENT", "至少选择一份资料")

    query = (
        db.select(Material)
        .where(Material.id.in_(ordered_ids))
        .options(selectinload(Material.current_version).selectinload(MaterialVersion.chunks))
    )
    materials_by_id = {material.id: material for material in db.session.scalars(query).all()}

    missing_ids = [material_id for material_id in ordered_ids if material_id not in materials_by_id]
    if missing_ids:
        raise APIError(404, "NOT_FOUND", "部分资料不存在", {"material_ids": missing_ids})

    unavailable_ids = [
        material_id
        for material_id in ordered_ids
        if not materials_by_id[material_id].current_version
        or materials_by_id[material_id].current_version.status != "ready"
    ]
    if unavailable_ids:
        raise APIError(409, "MATERIAL_NOT_READY", "部分资料尚未解析完成", {"material_ids": unavailable_ids})

    documents = []
    for material_id in ordered_ids:
        material = materials_by_id[material_id]
        version = material.current_version
        sources = []
        for chunk in sorted(version.chunks, key=lambda item: item.chunk_index):
            sources.append(
                {
                    "source_id": chunk.id,
                    "chunk_index": chunk.chunk_index,
                    "locator": chunk.locator_json or {},
                    "text": chunk.text,
                }
            )
        documents.append(
            {
                "material_id": material.id,
                "version_id": version.id,
                "filename": material.filename,
                "sources": sources,
            }
        )

    return json.dumps({"documents": documents}, ensure_ascii=False, separators=(",", ":"))
