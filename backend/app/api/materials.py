import shutil
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Material, MaterialChunk, MaterialVersion
from ..services.document_extractor import SUPPORTED_EXTENSIONS
from ..services.language import normalize_language
from ..services.material_processor import delete_material_permanently, enqueue_material_processing
from .errors import APIError


bp = Blueprint("materials", __name__)


@bp.post("/materials/check-name")
def check_name():
    payload = request.get_json(silent=True) or {}
    filename = _clean_filename(payload.get("filename"))
    material = _find_by_name(filename)
    data = {"duplicate": material is not None}
    if material is not None:
        data["existing_material"] = {
            "id": material.id,
            "filename": material.filename,
            "current_version_id": material.current_version_id,
        }
    return jsonify({"data": data})


@bp.post("/materials")
def upload_material():
    uploaded_file = request.files.get("file")
    if uploaded_file is None:
        raise APIError(400, "INVALID_ARGUMENT", "缺少上传文件")

    upload_filename = _clean_filename(uploaded_file.filename)
    try:
        language = normalize_language(request.form.get("language"))
    except ValueError as error:
        raise APIError(400, "INVALID_ARGUMENT", "language 必须是 zh-CN 或 en") from error
    filename = upload_filename
    extension = Path(filename).suffix.casefold()
    if extension not in SUPPORTED_EXTENSIONS:
        raise APIError(415, "UNSUPPORTED_FILE_TYPE", "仅支持 PDF、PPT、Word、Markdown 和 TXT 文件")

    duplicate_action = request.form.get("duplicate_action")
    if duplicate_action not in (None, "", "replace", "keep_both"):
        raise APIError(400, "INVALID_ARGUMENT", "duplicate_action 必须是 replace 或 keep_both")

    existing = _find_by_name(filename)
    material = None
    is_new_material = False
    if existing is not None:
        if duplicate_action in (None, ""):
            raise APIError(
                409,
                "DUPLICATE_NAME",
                "资料库中已存在同名文件",
                {"existing_material": _duplicate_data(existing)},
            )
        if duplicate_action == "replace":
            material = _validate_replacement(existing)
        else:
            filename = _available_copy_name(filename)
    elif duplicate_action == "replace":
        raise APIError(409, "VERSION_CONFLICT", "要覆盖的同名资料已发生变化，请重新确认")

    if material is None:
        material = Material(
            filename=filename,
            normalized_filename=_normalize_filename(filename),
            file_type=extension.removeprefix("."),
        )
        db.session.add(material)
        db.session.flush()
        is_new_material = True

    version_no = (
        db.session.scalar(
            db.select(db.func.max(MaterialVersion.version_no)).where(MaterialVersion.material_id == material.id)
        )
        or 0
    ) + 1
    version_directory = Path(current_app.config["UPLOAD_FOLDER"]) / material.id / str(version_no)
    storage_path = version_directory / f"source{extension}"
    version = MaterialVersion(
        material_id=material.id,
        version_no=version_no,
        original_filename=upload_filename,
        storage_path=str(storage_path.resolve()),
        file_size=0,
        language=language,
        status="processing",
    )
    db.session.add(version)

    try:
        version_directory.mkdir(parents=True, exist_ok=False)
        uploaded_file.save(storage_path)
        version.file_size = storage_path.stat().st_size
        if version.file_size == 0:
            raise APIError(400, "EMPTY_FILE", "上传文件为空")
        db.session.commit()
    except APIError:
        db.session.rollback()
        shutil.rmtree(version_directory, ignore_errors=True)
        if is_new_material:
            _remove_empty_material_directory(material.id)
        raise
    except IntegrityError as error:
        db.session.rollback()
        shutil.rmtree(version_directory, ignore_errors=True)
        if is_new_material:
            _remove_empty_material_directory(material.id)
        raise APIError(409, "DUPLICATE_NAME", "同名资料已发生变化，请重新选择处理方式") from error
    except OSError as error:
        db.session.rollback()
        shutil.rmtree(version_directory, ignore_errors=True)
        if is_new_material:
            _remove_empty_material_directory(material.id)
        raise APIError(500, "FILE_SAVE_FAILED", "文件保存失败") from error

    enqueue_material_processing(version.id)
    return (
        jsonify(
            {
                "data": {
                    "material_id": material.id,
                    "version_id": version.id,
                    "filename": material.filename,
                    "language": version.language,
                    "status": "processing",
                }
            }
        ),
        202,
    )


@bp.get("/materials")
def list_materials():
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except ValueError as error:
        raise APIError(400, "INVALID_ARGUMENT", "page 和 page_size 必须是整数") from error

    query = db.select(Material).order_by(Material.created_at.desc())
    pagination = db.paginate(query, page=page, per_page=page_size, error_out=False)
    items = [material.to_list_dict() for material in pagination.items]
    return jsonify({"data": {"items": items, "total": pagination.total, "page": page, "page_size": page_size}})


@bp.get("/materials/<material_id>")
def get_material(material_id: str):
    material = db.session.get(Material, material_id)
    if material is None:
        raise APIError(404, "NOT_FOUND", "资料不存在")
    data = material.to_list_dict()
    data["knowledge_points_count"] = (
        len(material.current_version.knowledge_points) if material.current_version else 0
    )
    data["chunks_count"] = len(material.current_version.chunks) if material.current_version else 0
    data["versions"] = [
        {
            "id": version.id,
            "version_no": version.version_no,
            "status": version.status,
            "file_size": version.file_size,
            "language": version.language,
            "error_message": version.error_message,
            "uploaded_at": version.uploaded_at.isoformat(),
            "ready_at": version.ready_at.isoformat() if version.ready_at else None,
        }
        for version in material.versions
    ]
    return jsonify({"data": data})


@bp.delete("/materials/<material_id>")
def delete_material(material_id: str):
    material = db.session.get(Material, material_id)
    if material is None:
        raise APIError(404, "NOT_FOUND", "资料不存在")
    try:
        delete_material_permanently(material)
    except OSError as error:
        current_app.logger.exception("Failed to delete material files for %s", material_id)
        raise APIError(500, "DELETE_FAILED", "资料文件删除失败") from error
    return "", 204


@bp.post("/materials/<material_id>/retry")
def retry_material(material_id: str):
    material = db.session.get(Material, material_id)
    if material is None:
        raise APIError(404, "NOT_FOUND", "资料不存在")
    latest = material.versions[-1] if material.versions else None
    if latest is None or latest.status != "failed":
        raise APIError(409, "INVALID_STATUS", "只有解析失败的资料可以重试")
    latest.status = "processing"
    latest.error_message = None
    latest.ready_at = None
    db.session.commit()
    enqueue_material_processing(latest.id)
    return jsonify({"data": {"material_id": material.id, "version_id": latest.id, "status": "processing"}}), 202


@bp.put("/materials/<material_id>/note")
def save_note(material_id: str):
    from .progress import save_material_note

    return save_material_note(material_id)


@bp.get("/materials/<material_id>/versions/<version_id>/sources/<chunk_id>")
def get_source(material_id: str, version_id: str, chunk_id: str):
    query = (
        db.select(MaterialChunk)
        .join(MaterialVersion, MaterialChunk.version_id == MaterialVersion.id)
        .where(
            MaterialChunk.id == chunk_id,
            MaterialChunk.version_id == version_id,
            MaterialVersion.material_id == material_id,
        )
    )
    chunk = db.session.scalar(query)
    if chunk is None:
        raise APIError(404, "NOT_FOUND", "资料来源不存在")
    version = chunk.version
    return jsonify(
        {
            "data": {
                "source_id": chunk.id,
                "material_id": material_id,
                "filename": version.material.filename,
                "version_id": version.id,
                "locator": chunk.locator_json,
                "text": chunk.text,
            }
        }
    )


def _clean_filename(value) -> str:
    if not isinstance(value, str):
        raise APIError(400, "INVALID_ARGUMENT", "filename 必须是字符串")
    filename = value.replace("\\", "/").split("/")[-1].strip()
    if not filename or filename in {".", ".."}:
        raise APIError(400, "INVALID_ARGUMENT", "文件名不能为空")
    if len(filename) > 512 or "\x00" in filename:
        raise APIError(400, "INVALID_ARGUMENT", "文件名无效或过长")
    return filename


def _normalize_filename(filename: str) -> str:
    return filename.strip().casefold()


def _find_by_name(filename: str):
    return db.session.scalar(
        db.select(Material).where(Material.normalized_filename == _normalize_filename(filename))
    )


def _duplicate_data(material: Material) -> dict:
    return {
        "id": material.id,
        "filename": material.filename,
        "current_version_id": material.current_version_id,
    }


def _validate_replacement(existing: Material) -> Material:
    target_material_id = request.form.get("target_material_id")
    if not target_material_id or "expected_version_id" not in request.form:
        raise APIError(400, "INVALID_ARGUMENT", "覆盖必须提供 target_material_id 和 expected_version_id")
    expected_version_id = request.form.get("expected_version_id", "")
    if existing.id != target_material_id or (existing.current_version_id or "") != expected_version_id:
        raise APIError(
            409,
            "VERSION_CONFLICT",
            "同名资料版本已发生变化，请重新确认",
            {"existing_material": _duplicate_data(existing)},
        )
    return existing


def _available_copy_name(filename: str) -> str:
    path = Path(filename)
    for number in range(1, 10_000):
        candidate = f"{path.stem} ({number}){path.suffix}"
        if _find_by_name(candidate) is None:
            return candidate
    raise APIError(409, "DUPLICATE_NAME", "无法为保留的资料生成可用文件名")


def _remove_empty_material_directory(material_id: str) -> None:
    directory = Path(current_app.config["UPLOAD_FOLDER"]) / material_id
    try:
        directory.rmdir()
    except OSError:
        pass
