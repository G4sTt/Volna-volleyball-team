"""Create catalog tables without resetting an existing PostgreSQL volume."""
import os
from pathlib import Path

from .database import get_connection


def init_catalog():
    path = Path(os.getenv("SCHEMA_PATH", "/app/schema.sql"))
    sql = path.read_text(encoding="utf-8-sig")
    with get_connection() as connection:
        # Avoid concurrent catalog initialization by backend replicas.
        connection.execute("SELECT pg_advisory_xact_lock(58173429)")
        connection.execute(sql, prepare=False)
