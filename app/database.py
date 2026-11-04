"""Database connections and connectivity checks."""

import psycopg

from app.config import Settings


def connect_database(settings: Settings) -> psycopg.Connection:
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        connect_timeout=5,
        options="-c statement_timeout=5000 -c timezone=UTC",
    )


def check_database(settings: Settings) -> bool:
    with connect_database(settings) as connection:
        row = connection.execute("SELECT 1").fetchone()
        return row == (1,)
