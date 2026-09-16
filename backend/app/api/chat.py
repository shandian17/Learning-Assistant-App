import json
import re
from datetime import timedelta

from flask import Blueprint, jsonify, request

from ..extensions import db
from ..models import ChatMessage, ChatSession, ChatSessionMaterial
from ..models.base import utc_now
from ..services.ai_prompts import CHAT_SYSTEM_PROMPT
from ..services.chunk_search import load_ready_materials, search_relevant_chunks
from ..services.llm_client import LLMClient, LLMConfigurationError, LLMResponseError, LLMServiceError
from .errors import APIError


bp = Blueprint("chat", __name__)


@bp.get("/chat/sessions")
def list_sessions():
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except ValueError as error:
        raise APIError(400, "INVALID_ARGUMENT", "page 和 page_size 必须是整数") from error
    pagination = db.paginate(
        db.select(ChatSession).order_by(ChatSession.updated_at.desc(), ChatSession.id.desc()),
        page=page,
        per_page=page_size,
        error_out=False,
    )
    items = []
    for session in pagination.items:
        last_message = session.messages[-1] if session.messages else None
        items.append(
            {
                "id": session.id,
                "material_scope": [
                    {"material_id": link.material_id, "filename": link.material.filename}
                    for link in session.materials
                ],
                "message_count": len(session.messages),
                "last_message": (
                    {
                        "role": last_message.role,
                        "content": last_message.content,
                        "created_at": last_message.created_at.isoformat(),
                    }
                    if last_message
                    else None
                ),
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
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


@bp.post("/chat/sessions")
def create_session():
    payload = request.get_json(silent=True) or {}
    material_ids = payload.get("material_ids")
    if not isinstance(material_ids, list) or not all(isinstance(item, str) for item in material_ids):
        raise APIError(400, "INVALID_ARGUMENT", "material_ids 必须是资料 ID 数组")
    materials = load_ready_materials(material_ids)
    session = ChatSession()
    db.session.add(session)
    db.session.flush()
    for material in materials:
        db.session.add(ChatSessionMaterial(session_id=session.id, material_id=material.id))
    db.session.commit()
    return jsonify(
        {
            "data": {
                "session_id": session.id,
                "material_ids": [item.id for item in materials],
                "created_at": session.created_at.isoformat(),
            }
        }
    ), 201


@bp.delete("/chat/sessions/<session_id>")
def delete_session(session_id: str):
    session = _session_or_404(session_id)
    db.session.execute(db.delete(ChatMessage).where(ChatMessage.session_id == session.id))
    db.session.execute(db.delete(ChatSessionMaterial).where(ChatSessionMaterial.session_id == session.id))
    db.session.execute(db.delete(ChatSession).where(ChatSession.id == session.id))
    db.session.commit()
    return "", 204


@bp.get("/chat/sessions/<session_id>/messages")
def list_messages(session_id: str):
    _session_or_404(session_id)
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 50))))
    except ValueError as error:
        raise APIError(400, "INVALID_ARGUMENT", "page 和 page_size 必须是整数") from error
    query = (
        db.select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
    )
    pagination = db.paginate(query, page=page, per_page=page_size, error_out=False)
    return jsonify(
        {
            "data": {
                "items": [_message_data(item) for item in pagination.items],
                "total": pagination.total,
                "page": page,
                "page_size": page_size,
            }
        }
    )


@bp.post("/chat/sessions/<session_id>/messages")
def send_message(session_id: str):
    session = _session_or_404(session_id)
    payload = request.get_json(silent=True) or {}
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise APIError(400, "INVALID_ARGUMENT", "content 不能为空")
    content = content.strip()
    if len(content) > 10_000:
        raise APIError(400, "INVALID_ARGUMENT", "content 过长")

    history = list(
        db.session.scalars(
            db.select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(12)
        )
    )
    history.reverse()
    material_ids = [link.material_id for link in session.materials]
    search_query = "\n".join([item.content for item in history[-4:] if item.role == "user"] + [content])
    sources, matched = search_relevant_chunks(material_ids, search_query)

    model_input = {
        "current_question": content,
        "history": [{"role": item.role, "content": item.content} for item in history],
        "sources": sources,
    }
    db.session.rollback()
    try:
        answer = LLMClient().chat_text(
            [
                {"role": "system", "content": CHAT_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(model_input, ensure_ascii=False)},
            ],
            temperature=0.2,
        )
    except (LLMConfigurationError, LLMServiceError, LLMResponseError) as error:
        raise APIError(503, "AI_UNAVAILABLE", "大模型服务暂不可用，请稍后重试") from error

    source_by_id = {item["source_id"]: item for item in sources}
    cited_ids = list(dict.fromkeys(re.findall(r"\[([^\[\]\s]+)\]", answer)))
    citations = [
        {
            "material_id": source_by_id[source_id]["material_id"],
            "version_id": source_by_id[source_id]["version_id"],
            "chunk_id": source_id,
            "filename": source_by_id[source_id]["filename"],
            "locator": source_by_id[source_id]["locator"],
        }
        for source_id in cited_ids
        if source_id in source_by_id
    ]
    insufficient = "当前资料未提供足够依据" in answer
    evidence_status = "insufficient" if insufficient else ("sufficient" if matched and citations else "partial")
    session = _session_or_404(session_id)
    user_created_at = utc_now()
    user_message = ChatMessage(
        session_id=session_id,
        role="user",
        content=content,
        created_at=user_created_at,
    )
    db.session.add(user_message)
    db.session.flush()
    assistant = ChatMessage(
        session_id=session_id,
        role="assistant",
        content=answer,
        evidence_status=evidence_status,
        citations_json=citations,
        created_at=user_created_at + timedelta(microseconds=1),
    )
    db.session.add(assistant)
    session.updated_at = utc_now()
    db.session.commit()
    return jsonify(
        {
            "data": {
                "user_message_id": user_message.id,
                "assistant_message": _message_data(assistant),
            }
        }
    ), 201


def _session_or_404(session_id: str) -> ChatSession:
    session = db.session.get(ChatSession, session_id)
    if session is None:
        raise APIError(404, "NOT_FOUND", "对话不存在")
    return session


def _message_data(message: ChatMessage) -> dict:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "citations": message.citations_json or [],
        "evidence_status": message.evidence_status,
        "created_at": message.created_at.isoformat(),
    }
