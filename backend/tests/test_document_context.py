import json

import pytest

from app.api.errors import APIError
from app.extensions import db
from app.models import Material, MaterialChunk, MaterialVersion
from app.services import build_document_context


def _ready_material(filename: str, texts: list[str]) -> Material:
    material = Material(
        filename=filename,
        normalized_filename=filename.casefold(),
        file_type="pdf",
    )
    db.session.add(material)
    db.session.flush()

    version = MaterialVersion(
        material_id=material.id,
        version_no=1,
        original_filename=filename,
        storage_path=f"uploads/{material.id}/1/{filename}",
        file_size=100,
        status="ready",
    )
    db.session.add(version)
    db.session.flush()

    for index, text in enumerate(texts):
        db.session.add(
            MaterialChunk(
                version_id=version.id,
                chunk_index=index,
                text=text,
                locator_json={"page": index + 1},
            )
        )
    material.current_version_id = version.id
    db.session.commit()
    return material


def test_context_contains_every_chunk_in_document_and_chunk_order(app):
    with app.app_context():
        second = _ready_material("第二份.pdf", ["第二份唯一内容"])
        first = _ready_material("第一份.pdf", ["第一段", "第二段"])

        context = json.loads(build_document_context([first.id, second.id]))

        assert [item["filename"] for item in context["documents"]] == ["第一份.pdf", "第二份.pdf"]
        assert [item["text"] for item in context["documents"][0]["sources"]] == ["第一段", "第二段"]
        assert context["documents"][1]["sources"][0]["text"] == "第二份唯一内容"


def test_context_rejects_unready_material(app):
    with app.app_context():
        material = Material(filename="等待.txt", normalized_filename="等待.txt", file_type="txt")
        db.session.add(material)
        db.session.commit()

        with pytest.raises(APIError) as error:
            build_document_context([material.id])

        assert error.value.status == 409
        assert error.value.code == "MATERIAL_NOT_READY"
