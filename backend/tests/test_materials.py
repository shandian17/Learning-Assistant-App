import io
from pathlib import Path
from threading import Event
from time import sleep

from app.extensions import db
from app.models import (
    Assessment,
    AssessmentAnswer,
    AssessmentMaterial,
    AssessmentQuestion,
    AssessmentSubmission,
    ChatMessage,
    ChatSession,
    ChatSessionMaterial,
    KnowledgePoint,
    KnowledgePointSource,
    Material,
    MaterialChunk,
    MaterialNote,
    MaterialVersion,
)
from app.services import material_processor
from app.services.document_extractor import ExtractedBlock


def _upload(client, name: str, content: bytes, **fields):
    data = {"file": (io.BytesIO(content), name), **fields}
    return client.post("/api/v1/materials", data=data, content_type="multipart/form-data")


def test_upload_runs_full_storage_extraction_and_database_pipeline(app, client):
    response = _upload(client, "课程.md", "# 注意力\n查询、键和值。".encode())
    assert response.status_code == 202
    result = response.get_json()["data"]
    assert result["status"] == "processing"

    with app.app_context():
        material = db.session.get(Material, result["material_id"])
        version = db.session.get(MaterialVersion, result["version_id"])
        chunks = list(
            db.session.scalars(
                db.select(MaterialChunk)
                .where(MaterialChunk.version_id == version.id)
                .order_by(MaterialChunk.chunk_index)
            )
        )
        assert material.current_version_id == version.id
        assert version.status == "ready"
        assert Path(version.storage_path).read_bytes() == "# 注意力\n查询、键和值。".encode()
        assert [chunk.text for chunk in chunks] == ["# 注意力\n查询、键和值。"]
        assert chunks[0].locator_json["heading"] == "注意力"
        source_url = (
            f"/api/v1/materials/{material.id}/versions/{version.id}/sources/{chunks[0].id}"
        )

    listed = client.get("/api/v1/materials").get_json()["data"]["items"]
    assert listed[0]["status"] == "ready"
    source = client.get(source_url)
    assert source.status_code == 200
    assert source.get_json()["data"]["text"] == "# 注意力\n查询、键和值。"


def test_upload_records_interface_language_without_translating_content(app, client):
    content = "Transformer 与注意力机制 stay unchanged."
    response = _upload(client, "mixed.txt", content.encode(), language="en")
    assert response.status_code == 202
    result = response.get_json()["data"]
    assert result["language"] == "en"

    with app.app_context():
        version = db.session.get(MaterialVersion, result["version_id"])
        assert version.language == "en"
        assert version.chunks[0].text == content

    listed = client.get("/api/v1/materials").get_json()["data"]["items"]
    assert listed[0]["language"] == "en"


def test_upload_returns_while_background_parser_is_running(app, client, monkeypatch):
    started = Event()
    release = Event()

    def slow_extract(_path, _extension):
        started.set()
        assert release.wait(5)
        return [ExtractedBlock("后台解析结果", {"start_line": 1, "end_line": 1})]

    app.config["PROCESS_MATERIALS_INLINE"] = False
    monkeypatch.setattr(material_processor, "extract_document", slow_extract)
    response = _upload(client, "后台.txt", "稍后解析".encode())
    assert response.status_code == 202
    result = response.get_json()["data"]
    assert started.wait(2)

    with app.app_context():
        assert db.session.get(MaterialVersion, result["version_id"]).status == "processing"

    release.set()
    for _ in range(100):
        with app.app_context():
            version = db.session.get(MaterialVersion, result["version_id"])
            if version.status == "ready":
                assert version.chunks[0].text == "后台解析结果"
                break
        sleep(0.02)
    else:
        raise AssertionError("后台解析没有完成")


def test_duplicate_keep_replace_and_physical_delete(app, client):
    first = _upload(client, "重复.txt", "第一版".encode()).get_json()["data"]
    check = client.post("/api/v1/materials/check-name", json={"filename": "重复.TXT"})
    assert check.get_json()["data"]["duplicate"] is True

    conflict = _upload(client, "重复.txt", "未确认".encode())
    assert conflict.status_code == 409
    assert conflict.get_json()["error"]["code"] == "DUPLICATE_NAME"

    kept = _upload(client, "重复.txt", "独立副本".encode(), duplicate_action="keep_both")
    assert kept.status_code == 202
    assert kept.get_json()["data"]["filename"] == "重复 (1).txt"

    replaced = _upload(
        client,
        "重复.txt",
        "第二版".encode(),
        duplicate_action="replace",
        target_material_id=first["material_id"],
        expected_version_id=first["version_id"],
    )
    assert replaced.status_code == 202
    replacement = replaced.get_json()["data"]

    with app.app_context():
        material = db.session.get(Material, first["material_id"])
        assert material.current_version_id == replacement["version_id"]
        assert len(material.versions) == 2
        material_directory = Path(material.versions[0].storage_path).parents[1]
        assert material_directory.exists()
        replacement_chunk = db.session.scalar(
            db.select(MaterialChunk).where(MaterialChunk.version_id == replacement["version_id"])
        )
        knowledge_point = KnowledgePoint(
            version_id=replacement["version_id"], name="测试知识点", description="用于删除验证"
        )
        chat_session = ChatSession()
        assessment = Assessment(status="graded", question_counts_json={"short_answer": 1})
        db.session.add_all([knowledge_point, chat_session, assessment])
        db.session.flush()
        question = AssessmentQuestion(
            assessment_id=assessment.id,
            order_no=1,
            type="short_answer",
            difficulty="easy",
            stem="测试题",
            correct_answer_json="答案",
            rubric_json={"full_credit": "正确"},
            max_score=1,
            knowledge_point_id=knowledge_point.id,
            knowledge_point_name_snapshot=knowledge_point.name,
            sources_snapshot_json=[{"source_id": replacement_chunk.id}],
        )
        submission = AssessmentSubmission(
            assessment_id=assessment.id,
            request_id="00000000-0000-0000-0000-000000000001",
            status="graded",
            total_score=1,
            max_score=1,
            duration_seconds=5,
        )
        db.session.add_all(
            [
                KnowledgePointSource(
                    knowledge_point_id=knowledge_point.id, chunk_id=replacement_chunk.id
                ),
                ChatSessionMaterial(session_id=chat_session.id, material_id=material.id),
                ChatMessage(session_id=chat_session.id, role="user", content="问题"),
                AssessmentMaterial(
                    assessment_id=assessment.id,
                    version_id=replacement["version_id"],
                    filename_snapshot=material.filename,
                ),
                question,
                submission,
                MaterialNote(material_id=material.id, content="笔记"),
            ]
        )
        db.session.flush()
        db.session.add(
            AssessmentAnswer(
                submission_id=submission.id,
                question_id=question.id,
                answer_json="答案",
                score=1,
            )
        )
        db.session.commit()

    deleted = client.delete(f"/api/v1/materials/{first['material_id']}")
    assert deleted.status_code == 204
    assert not material_directory.exists()

    with app.app_context():
        assert db.session.get(Material, first["material_id"]) is None
        assert db.session.scalar(
            db.select(db.func.count()).select_from(MaterialVersion).where(
                MaterialVersion.material_id == first["material_id"]
            )
        ) == 0
        assert db.session.scalar(
            db.select(db.func.count()).select_from(MaterialChunk).join(MaterialVersion).where(
                MaterialVersion.material_id == first["material_id"]
            )
        ) == 0
        assert db.session.scalar(db.select(db.func.count()).select_from(ChatSession)) == 0
        assert db.session.scalar(db.select(db.func.count()).select_from(Assessment)) == 0
        assert db.session.scalar(
            db.select(db.func.count()).select_from(KnowledgePoint).where(
                KnowledgePoint.version_id.in_([first["version_id"], replacement["version_id"]])
            )
        ) == 0
        assert db.session.scalar(db.select(db.func.count()).select_from(MaterialNote)) == 0


def test_extraction_failure_is_saved_and_can_be_retried(app, client):
    uploaded = _upload(client, "损坏.pdf", b"not a pdf")
    assert uploaded.status_code == 202
    result = uploaded.get_json()["data"]

    with app.app_context():
        version = db.session.get(MaterialVersion, result["version_id"])
        assert version.status == "failed"
        assert version.error_message
        assert version.chunks == []

    retried = client.post(f"/api/v1/materials/{result['material_id']}/retry")
    assert retried.status_code == 202
    with app.app_context():
        version = db.session.get(MaterialVersion, result["version_id"])
        assert version.status == "failed"
        assert version.error_message
