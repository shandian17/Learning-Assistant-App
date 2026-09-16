import click
from flask import Flask

from .extensions import db


def register_commands(app: Flask) -> None:
    @app.cli.command("init-db")
    def init_db() -> None:
        """Create missing database tables without deleting existing data."""
        db.create_all()
        click.echo("Database tables are ready.")

    @app.cli.command("list-tables")
    def list_tables() -> None:
        """Print the tables visible to the configured database."""
        inspector = db.inspect(db.engine)
        for table_name in sorted(inspector.get_table_names()):
            click.echo(table_name)
