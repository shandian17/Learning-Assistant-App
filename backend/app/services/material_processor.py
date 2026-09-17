import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from uuid import uuid4

from flask import current_app

from ..extensions import db
from ..models import (
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
from ..models.base import utc_now
from .document_extractor import DocumentExtractionError, extract_document


_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="material-parser")
_locks_guard = Lock()
_material_locks: dict[str, Lock] = {}


def material_lock(material_id: str) -> Lock:
    with _locks_guard:
        return _material_locks.setdefault(material_id, Lock())


def enqueue_material_processing(version_id: str) -> None:
    app = current_app._get_current_object()
    if app.config.get("PROCESS_MATERIALS_INLINE"):
        process_material_version(app, version_id)
    else:
        _executor.submit(process_material_version, app, version_id)


def process_material_version(app, version_id: str) -> None:
    with app.app_context():
        version = db.session.get(MaterialVersion, version_id)
        if version is None:
            return
        lock = material_lock(version.material_id)
        db.session.remove()

        with lock:
            version = db.session.get(MaterialVersion, version_id)
            if version is None or version.status != "processing":
                return
            storage_path = _safe_storage_path(Path(version.storage_path), Path(app.config["UPLOAD_FOLDER"]))
            extension = Path(version.original_filename).suffix.casefold()
            db.session.remove()

            try:
                blocks = extract_document(storage_path, extension)
                version = db.session.get(MaterialVersion, version_id)
                if version is None or version.status != "processing":
                    return
                point_ids = list(
                    db.session.scalars(
                        db.select(KnowledgePoint.id).where(KnowledgePoint.version_id == version_id)
                    )
                )
                if point_ids:
                    db.session.execute(
                        db.delete(KnowledgePointSource).where(
                            KnowledgePointSource.knowledge_point_id.in_(point_ids)
                        )
                    )
                    db.session.execute(db.delete(KnowledgePoint).where(KnowledgePoint.id.in_(point_ids)))
                db.session.execute(db.delete(MaterialChunk).where(MaterialChunk.version_id == version_id))
                for index, block in enumerate(blocks):
                    chunk = MaterialChunk(
                        version_id=version_id,
                        chunk_index=index,
                        text=block.text,
                        locator_json=block.locator,
                    )
                    db.session.add(chunk)
                    db.session.flush()
                    point = KnowledgePoint(
                        version_id=version_id,
                        name=_knowledge_point_name(block.locator, block.text, index, version.language),
                        description=block.text.strip()[:240],
                    )
                    db.session.add(point)
                    db.session.flush()
                    db.session.add(KnowledgePointSource(knowledge_point_id=point.id, chunk_id=chunk.id))
                version.status = "ready"
                version.error_message = None
                version.ready_at = utc_now()
                version.material.current_version_id = version.id
                db.session.commit()
            except DocumentExtractionError as error:
                _mark_failed(version_id, str(error))
            except Exception:
                app.logger.exception("Material parsing failed for version %s", version_id)
                _mark_failed(version_id, "解析过程中发生错误，请重试")
            finally:
                db.session.remove()


def _knowledge_point_name(locator: dict, text: str, index: int, language: str = "zh-CN") -> str:
    for key in ("heading", "title"):
        value = locator.get(key)
        if value:
            return str(value)[:512]
    labels = (
        (("page", "Page {value}"), ("slide", "Slide {value}"))
        if language == "en"
        else (("page", "第 {value} 页"), ("slide", "第 {value} 张幻灯片"))
    )
    for key, label in labels:
        value = locator.get(key)
        if value is not None:
            return label.format(value=value)
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    fallback = f"Knowledge point {index + 1}" if language == "en" else f"知识点 {index + 1}"
    return (first_line[:80] or fallback)[:512]


def _mark_failed(version_id: str, message: str) -> None:
    db.session.rollback()
    version = db.session.get(MaterialVersion, version_id)
    if version is not None and version.status == "processing":
        version.status = "failed"
        version.error_message = message
        version.ready_at = None
        db.session.commit()


def _safe_storage_path(path: Path, upload_root: Path) -> Path:
    resolved_root = upload_root.resolve()
    resolved_path = path.resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise DocumentExtractionError("资料存储位置无效")
    if not resolved_path.is_file():
        raise DocumentExtractionError("上传文件不存在")
    return resolved_path


def delete_material_permanently(material: Material) -> None:
    upload_root = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    material_directory = (upload_root / material.id).resolve()
    if material_directory.parent != upload_root:
        raise RuntimeError("资料目录越出上传目录")

    lock = material_lock(material.id)
    with lock:
        staged_directory = None
        if material_directory.exists():
            trash_root = upload_root / ".trash"
            trash_root.mkdir(parents=True, exist_ok=True)
            staged_directory = trash_root / f"{material.id}-{uuid4()}"
            material_directory.replace(staged_directory)

        try:
            _delete_related_database_rows(material.id)
            db.session.commit()
        except Exception:
            db.session.rollback()
            if staged_directory and staged_directory.exists():
                staged_directory.replace(material_directory)
            raise

        if staged_directory and staged_directory.exists():
            shutil.rmtree(staged_directory)
            try:
                staged_directory.parent.rmdir()
            except OSError:
                pass


def _delete_related_database_rows(material_id: str) -> None:
    version_ids = list(
        db.session.scalars(db.select(MaterialVersion.id).where(MaterialVersion.material_id == material_id))
    )

    assessment_ids = []
    if version_ids:
        assessment_ids = list(
            db.session.scalars(
                db.select(AssessmentMaterial.assessment_id)
                .where(AssessmentMaterial.version_id.in_(version_ids))
                .distinct()
            )
        )
    if assessment_ids:
        question_ids = list(
            db.session.scalars(
                db.select(AssessmentQuestion.id).where(AssessmentQuestion.assessment_id.in_(assessment_ids))
            )
        )
        submission_ids = list(
            db.session.scalars(
                db.select(AssessmentSubmission.id).where(AssessmentSubmission.assessment_id.in_(assessment_ids))
            )
        )
        if submission_ids:
            db.session.execute(
                db.delete(AssessmentAnswer).where(AssessmentAnswer.submission_id.in_(submission_ids))
            )
        if question_ids:
            db.session.execute(db.delete(AssessmentAnswer).where(AssessmentAnswer.question_id.in_(question_ids)))
        db.session.execute(
            db.delete(AssessmentSubmission).where(AssessmentSubmission.assessment_id.in_(assessment_ids))
        )
        db.session.execute(db.delete(AssessmentQuestion).where(AssessmentQuestion.assessment_id.in_(assessment_ids)))
        db.session.execute(db.delete(AssessmentMaterial).where(AssessmentMaterial.assessment_id.in_(assessment_ids)))
        db.session.execute(db.delete(Assessment).where(Assessment.id.in_(assessment_ids)))

    session_ids = list(
        db.session.scalars(
            db.select(ChatSessionMaterial.session_id).where(ChatSessionMaterial.material_id == material_id)
        )
    )
    if session_ids:
        db.session.execute(db.delete(ChatMessage).where(ChatMessage.session_id.in_(session_ids)))
        db.session.execute(db.delete(ChatSessionMaterial).where(ChatSessionMaterial.session_id.in_(session_ids)))
        db.session.execute(db.delete(ChatSession).where(ChatSession.id.in_(session_ids)))

    knowledge_point_ids = []
    if version_ids:
        knowledge_point_ids = list(
            db.session.scalars(db.select(KnowledgePoint.id).where(KnowledgePoint.version_id.in_(version_ids)))
        )
    if knowledge_point_ids:
        db.session.execute(
            db.delete(KnowledgePointSource).where(
                KnowledgePointSource.knowledge_point_id.in_(knowledge_point_ids)
            )
        )
        db.session.execute(db.delete(KnowledgePoint).where(KnowledgePoint.id.in_(knowledge_point_ids)))

    if version_ids:
        db.session.execute(db.delete(MaterialChunk).where(MaterialChunk.version_id.in_(version_ids)))
    db.session.execute(db.delete(MaterialNote).where(MaterialNote.material_id == material_id))
    db.session.execute(
        db.update(Material).where(Material.id == material_id).values(current_version_id=None)
    )
    if version_ids:
        db.session.execute(db.delete(MaterialVersion).where(MaterialVersion.id.in_(version_ids)))
    db.session.execute(db.delete(Material).where(Material.id == material_id))
