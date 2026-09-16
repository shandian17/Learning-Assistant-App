from app.extensions import db


EXPECTED_TABLES = {
    "assessment_answers",
    "assessment_materials",
    "assessment_questions",
    "assessment_submissions",
    "assessments",
    "chat_messages",
    "chat_session_materials",
    "chat_sessions",
    "knowledge_point_sources",
    "knowledge_points",
    "material_chunks",
    "material_notes",
    "material_versions",
    "materials",
    "report_generation_requests",
    "weekly_reports",
}


def test_database_contains_all_requirement_tables(app):
    with app.app_context():
        assert set(db.inspect(db.engine).get_table_names()) == EXPECTED_TABLES


def test_health_and_empty_material_list(client):
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.get_json() == {"data": {"status": "ok"}}

    materials = client.get("/api/v1/materials")
    assert materials.status_code == 200
    assert materials.get_json() == {
        "data": {"items": [], "total": 0, "page": 1, "page_size": 20}
    }


def test_empty_progress_and_unknown_api_use_json_errors(client):
    progress = client.get("/api/v1/progress/summary")
    assert progress.status_code == 200
    assert progress.get_json()["data"]["total_materials"] == 0
    assert progress.get_json()["data"]["coverage_rate"] is None

    missing = client.get("/api/v1/does-not-exist")
    assert missing.status_code == 404
    assert missing.get_json()["error"]["code"] == "NOT_FOUND"


def test_frontend_api_routes_are_registered(app):
    routes = {
        (rule.rule, method)
        for rule in app.url_map.iter_rules()
        for method in rule.methods - {"HEAD", "OPTIONS"}
    }
    expected = {
        ("/api/v1/materials", "GET"),
        ("/api/v1/materials", "POST"),
        ("/api/v1/materials/check-name", "POST"),
        ("/api/v1/materials/<material_id>", "GET"),
        ("/api/v1/materials/<material_id>", "DELETE"),
        ("/api/v1/materials/<material_id>/retry", "POST"),
        ("/api/v1/materials/<material_id>/note", "PUT"),
        ("/api/v1/chat/sessions", "GET"),
        ("/api/v1/chat/sessions", "POST"),
        ("/api/v1/chat/sessions/<session_id>", "DELETE"),
        ("/api/v1/chat/sessions/<session_id>/messages", "GET"),
        ("/api/v1/chat/sessions/<session_id>/messages", "POST"),
        ("/api/v1/assessments", "GET"),
        ("/api/v1/assessments", "POST"),
        ("/api/v1/assessments/<assessment_id>", "GET"),
        ("/api/v1/assessments/<assessment_id>/submissions", "POST"),
        ("/api/v1/assessments/<assessment_id>/result", "GET"),
        ("/api/v1/assessments/<assessment_id>/retry-grading", "POST"),
        ("/api/v1/progress/summary", "GET"),
        ("/api/v1/progress/knowledge-points", "GET"),
        ("/api/v1/progress/materials", "GET"),
        ("/api/v1/weekly-reports", "GET"),
        ("/api/v1/weekly-reports", "POST"),
        ("/api/v1/weekly-reports/<report_id>", "GET"),
        ("/api/v1/weekly-reports/<report_id>/download", "GET"),
    }
    assert expected <= routes
