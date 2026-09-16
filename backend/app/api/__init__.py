from flask import Flask

from .assessments import bp as assessments_bp
from .chat import bp as chat_bp
from .errors import register_error_handlers
from .health import bp as health_bp
from .materials import bp as materials_bp
from .progress import bp as progress_bp
from .reports import bp as reports_bp


def register_blueprints(app: Flask) -> None:
    for blueprint in (health_bp, materials_bp, chat_bp, assessments_bp, progress_bp, reports_bp):
        app.register_blueprint(blueprint, url_prefix="/api/v1")


__all__ = ["register_blueprints", "register_error_handlers"]
