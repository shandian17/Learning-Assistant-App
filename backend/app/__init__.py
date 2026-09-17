import os
from pathlib import Path

from flask import Flask

from .config import BACKEND_DIR, CONFIGS
from .extensions import cors, db


def create_app(config_override=None) -> Flask:
    app = Flask(__name__, instance_path=str(BACKEND_DIR / "instance"), instance_relative_config=True)
    environment = os.getenv("APP_ENV", "development").lower()
    app.config.from_object(CONFIGS.get(environment, CONFIGS["development"]))
    if config_override:
        if isinstance(config_override, dict):
            app.config.from_mapping(config_override)
        else:
            app.config.from_object(config_override)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    cors.init_app(app, resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}})
    app.json.ensure_ascii = False
    app.json.sort_keys = False

    # Import registers every model with SQLAlchemy before CLI commands run.
    from . import models  # noqa: F401
    from .api import register_blueprints, register_error_handlers
    from .commands import register_commands
    from .schema_compat import ensure_compatibility_columns

    with app.app_context():
        ensure_compatibility_columns()

    register_blueprints(app)
    register_error_handlers(app)
    register_commands(app)
    return app
