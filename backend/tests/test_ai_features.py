import io
import json
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.extensions import db
from app.models import MaterialChunk
from app.services import LLMServiceError


def _upload(client, filename="课程.txt", content="注意力机制会根据相关性权重聚合上下文信息。"):
    response = client.post(
        "/api/v1/materials",
        data={"file": (io.BytesIO(content.encode()), filename)},
        content_type="multipart/form-data",
    )
    assert response.status_code == 202
    return response.get_json()["data"]


def test_chat_uses_relevant_database_chunks_and_real_citation(app, client, monkeypatch):
    uploaded = _upload(client)
    with app.app_context():
        source_id = db.session.scalar(
            db.select(MaterialChunk.id).where(MaterialChunk.version_id == uploaded["version_id"])
        )
    captured = {}

    def fake_chat_text(_self, messages, **_options):
        captured.update(json.loads(messages[1]["content"]))
        return f"注意力机制按相关性聚合上下文。[{source_id}] [fake-source]"

    monkeypatch.setattr("app.api.chat.LLMClient.chat_text", fake_chat_text)
    created = client.post("/api/v1/chat/sessions", json={"material_ids": [uploaded["material_id"]]})
    session_id = created.get_json()["data"]["session_id"]
    response = client.post(
        f"/api/v1/chat/sessions/{session_id}/messages",
        json={"content": "注意力机制如何聚合上下文？"},
    )
    assert response.status_code == 201
    answer = response.get_json()["data"]["assistant_message"]
    assert answer["evidence_status"] == "sufficient"
    assert [item["chunk_id"] for item in answer["citations"]] == [source_id]
    assert captured["sources"][0]["source_id"] == source_id
    history = client.get(f"/api/v1/chat/sessions/{session_id}/messages").get_json()["data"]
    assert [item["role"] for item in history["items"]] == ["user", "assistant"]
    sessions = client.get("/api/v1/chat/sessions").get_json()["data"]
    assert sessions["total"] == 1
    assert sessions["items"][0]["message_count"] == 2
    assert client.delete(f"/api/v1/chat/sessions/{session_id}").status_code == 204
    assert client.get("/api/v1/chat/sessions").get_json()["data"]["total"] == 0


def test_failed_chat_call_does_not_store_half_a_conversation(client, monkeypatch):
    uploaded = _upload(client)
    created = client.post("/api/v1/chat/sessions", json={"material_ids": [uploaded["material_id"]]})
    session_id = created.get_json()["data"]["session_id"]

    def fail_chat(_self, _messages, **_options):
        raise LLMServiceError("offline")

    monkeypatch.setattr("app.api.chat.LLMClient.chat_text", fail_chat)
    failed = client.post(
        f"/api/v1/chat/sessions/{session_id}/messages",
        json={"content": "这个概念是什么？"},
    )
    assert failed.status_code == 503
    assert failed.get_json()["error"]["code"] == "AI_UNAVAILABLE"
    assert client.get(f"/api/v1/chat/sessions/{session_id}/messages").get_json()["data"]["total"] == 0


def test_assessment_generation_grading_history_and_weekly_report(app, client, monkeypatch):
    uploaded = _upload(client)

    def fake_chat_json(_self, messages, **_options):
        payload = json.loads(messages[1]["content"])
        if "question_counts" in payload:
            source = payload["documents"][0]["sources"][0]
            types = ["single_choice"] * 3 + ["true_false", "short_answer"]
            difficulties = ["easy", "easy", "medium", "medium", "hard"]
            questions = []
            for index, (question_type, difficulty) in enumerate(zip(types, difficulties), start=1):
                questions.append(
                    {
                        "order_no": index,
                        "type": question_type,
                        "difficulty": difficulty,
                        "stem": f"第 {index} 题",
                        "options": (
                            [{"id": item, "text": f"选项 {item}"} for item in "ABCD"]
                            if question_type == "single_choice"
                            else []
                        ),
                        "correct_answer": "A" if question_type == "single_choice" else (True if question_type == "true_false" else "按相关性权重聚合上下文"),
                        "max_score": 1,
                        "knowledge_point_id": source["knowledge_point_id"],
                        "sources": [{"source_id": source["source_id"], "excerpt": "注意力按照相关程度汇总上下文"}],
                        "explanation": "资料明确说明",
                        "rubric": (
                            {
                                "key_points": ["相关性权重", "聚合上下文"],
                                "full_credit": "完整覆盖两个要点",
                                "half_credit": "覆盖一个要点",
                                "zero_credit": "没有有效要点",
                            }
                            if question_type == "short_answer"
                            else None
                        ),
                    }
                )
            return {"status": "ok", "reason": "", "questions": questions}
        if "questions" in payload:
            results = []
            for question in payload["questions"]:
                score = question["objective_score"] if question["objective_score"] is not None else 0.5
                results.append(
                    {
                        "question_id": question["question_id"],
                        "score": score,
                        "matched_points": [],
                        "missing_or_incorrect_points": [],
                        "feedback": "依据资料完成评分。",
                        "source_ids": [question["sources"][0]["source_id"]],
                    }
                )
            return {"status": "ok", "reason": "", "results": results}
        return {
            "title": "本周学习报告",
            "summary": "完成资料学习与测评。",
            "learned": ["理解注意力机制"],
            "weak_points": ["简答表达还可补全"],
            "next_week_suggestions": ["复习相关性权重"],
        }

    monkeypatch.setattr("app.services.assessment_service.LLMClient.chat_json", fake_chat_json)
    monkeypatch.setattr("app.services.report_service.LLMClient.chat_json", fake_chat_json)

    create = client.post(
        "/api/v1/assessments",
        json={
            "request_id": str(uuid4()),
            "material_ids": [uploaded["material_id"]],
            "question_counts": {"single_choice": 3, "true_false": 1, "short_answer": 1},
        },
    )
    assert create.status_code == 202
    assessment_id = create.get_json()["data"]["assessment_id"]
    detail = client.get(f"/api/v1/assessments/{assessment_id}").get_json()["data"]
    assert detail["status"] == "ready"
    assert [item["difficulty"] for item in detail["questions"]] == ["easy", "easy", "medium", "medium", "hard"]
    assert detail["questions"][0]["options"] == {
        "A": "选项 A",
        "B": "选项 B",
        "C": "选项 C",
        "D": "选项 D",
    }
    assert detail["questions"][3]["options"] == {"A": "正确", "B": "错误"}
    assert all("correct_answer" not in item for item in detail["questions"])

    answers = []
    for question in detail["questions"]:
        value = "A" if question["type"] == "single_choice" else (True if question["type"] == "true_false" else "相关性聚合")
        answers.append({"question_id": question["id"], "answer": value})
    submitted = client.post(
        f"/api/v1/assessments/{assessment_id}/submissions",
        json={
            "request_id": str(uuid4()),
            "answers": answers,
            "confirm_unanswered": True,
            "duration_seconds": 42,
        },
    )
    assert submitted.status_code == 202
    result = client.get(f"/api/v1/assessments/{assessment_id}/result").get_json()["data"]
    assert result["status"] == "graded"
    assert result["total_score"] == 4.5
    assert result["duration_seconds"] == 42
    assert result["questions"][0]["sources"][0]["excerpt"] == "注意力机制会根据相关性权重聚合上下文信息。"
    history = client.get("/api/v1/assessments?status=graded").get_json()["data"]
    assert history["total"] == 1
    assert history["items"][0]["score_rate"] == 90.0

    material_detail = client.get(f"/api/v1/materials/{uploaded['material_id']}").get_json()["data"]
    assert material_detail["knowledge_points_count"] == 1
    summary = client.get("/api/v1/progress/summary").get_json()["data"]
    assert summary["total_materials"] == 1
    assert summary["assessments_count"] == 1
    assert summary["average_score_rate"] == 90.0
    assert summary["coverage_rate"] == 100.0
    assert summary["weak_points_count"] == 1
    points = client.get("/api/v1/progress/knowledge-points").get_json()["data"]
    assert points["total"] == 1
    assert points["items"][0]["mastery_status"] == "partial"
    assert points["items"][0]["latest_score"] == 0.9
    weak = client.get("/api/v1/progress/weak-points").get_json()["data"]
    assert weak["total"] == 1
    materials = client.get("/api/v1/progress/materials").get_json()["data"]
    assert materials["items"][0]["partial_points"] == 1
    note = client.put(
        f"/api/v1/materials/{uploaded['material_id']}/note", json={"content": "复习注意力权重"}
    )
    assert note.status_code == 200
    assert note.get_json()["data"]["content"] == "复习注意力权重"
    scores = client.get("/api/v1/progress/scores").get_json()["data"]
    assert scores["total"] == 1
    assert scores["items"][0]["score_rate"] == 90.0

    today = datetime.now(ZoneInfo("America/Toronto")).date().isoformat()
    report_response = client.post(
        "/api/v1/weekly-reports",
        json={
            "request_id": str(uuid4()),
            "period_type": "custom",
            "date_from": today,
            "date_to": today,
            "timezone": "America/Toronto",
        },
    )
    assert report_response.status_code == 202
    report_id = report_response.get_json()["data"]["report_id"]
    report = client.get(f"/api/v1/weekly-reports/{report_id}").get_json()["data"]
    assert report["status"] == "ready"
    assert report["statistics"] == {
        "uploads_count": 1,
        "questions_count": 0,
        "assessments_count": 1,
        "average_score_rate": 90.0,
    }
    assert report["content"]["title"] == "本周学习报告"
    report_history = client.get("/api/v1/weekly-reports?status=ready").get_json()["data"]
    assert report_history["total"] == 1
    assert report_history["items"][0]["id"] == report_id
    download = client.get(f"/api/v1/weekly-reports/{report_id}/download")
    assert download.status_code == 200
    assert "attachment" in download.headers["Content-Disposition"]
    assert "本周学习报告" in download.get_data(as_text=True)
