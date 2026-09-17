from sqlalchemy import inspect, text

from .extensions import db


def ensure_compatibility_columns() -> None:
    """Add language columns to databases created before bilingual support."""
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    additions = {
        "material_versions": "language VARCHAR(8) NOT NULL DEFAULT 'zh-CN'",
        "assessments": "language VARCHAR(8) NOT NULL DEFAULT 'zh-CN'",
        "weekly_reports": "language VARCHAR(8) NOT NULL DEFAULT 'zh-CN'",
    }
    changed = False
    for table_name, definition in additions.items():
        if table_name not in tables:
            continue
        columns = {item["name"] for item in inspector.get_columns(table_name)}
        if "language" not in columns:
            db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {definition}"))
            changed = True
    if changed:
        db.session.commit()
