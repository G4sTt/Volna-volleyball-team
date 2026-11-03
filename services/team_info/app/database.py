import os
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row


def connection_config() -> dict:
    return {
        "host": os.getenv("DB_HOST", "team-info-db"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "dbname": os.getenv("DB_NAME", "team_info"),
        "user": os.getenv("DB_USER", "team_info_user"),
        "password": os.environ["DB_PASSWORD"],
        "connect_timeout": 10,
    }

@contextmanager
def get_connection():
    with psycopg.connect(
        **connection_config(),
        row_factory=dict_row,
    ) as connection:
        yield connection
