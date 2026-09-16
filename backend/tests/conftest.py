import pytest

from app import create_app
from app.extensions import db


@pytest.fixture()
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "PROCESS_MATERIALS_INLINE": True,
            "PROCESS_AI_INLINE": True,
            "LLM_BASE_URL": "https://provider.example/v1",
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "test-model",
        }
    )
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()


@pytest.fixture()
def client(app):
    return app.test_client()
