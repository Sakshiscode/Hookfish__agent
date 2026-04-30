"""
Database connection pool for the API server.
Reuses the same MySQL DB as the voice agent.
"""

import os
import pymysql
from pymysql.cursors import DictCursor
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    """Create a new database connection."""
    return pymysql.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", 14064)),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        ssl={"ssl": {}},
        cursorclass=DictCursor,
        autocommit=False,
    )


@contextmanager
def get_db():
    """Context manager for database connections. Auto-closes on exit."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute_query(sql: str, params=None, fetch_one=False, fetch_all=True):
    """Execute a query and return results."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if fetch_one:
                return cur.fetchone()
            if fetch_all:
                return cur.fetchall()
            return cur.lastrowid


def execute_insert(sql: str, params=None):
    """Execute an insert/update and return affected row count."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.lastrowid
