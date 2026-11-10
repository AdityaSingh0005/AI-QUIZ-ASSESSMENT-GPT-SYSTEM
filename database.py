"""Process-local, thread-safe PostgreSQL connection pool.

Each Gunicorn worker owns its own pool. Keep DB_POOL_MAX small enough that
WEB_CONCURRENCY * DB_POOL_MAX stays below the Neon connection limit. Prefer
Neon's pooled connection string for multi-worker deployments.
"""

import os
import threading

from psycopg2 import extensions
from psycopg2.pool import ThreadedConnectionPool
from dotenv import load_dotenv

load_dotenv()

_pool = None
_pool_lock = threading.Lock()
_pool_pid = None
_pool_dsn = None
_pool_slots = None


class PooledConnection:
    """Small proxy that returns the underlying connection on close()."""

    def __init__(self, raw_connection, pool, slots, default_cursor_factory=None):
        self._raw = raw_connection
        self._pool = pool
        self._slots = slots
        self._default_cursor_factory = default_cursor_factory
        self._returned = False
        self._return_lock = threading.Lock()

    def cursor(self, *args, **kwargs):
        if self._default_cursor_factory and "cursor_factory" not in kwargs:
            kwargs["cursor_factory"] = self._default_cursor_factory
        return self._raw.cursor(*args, **kwargs)

    def close(self):
        with self._return_lock:
            if self._returned:
                return
            self._returned = True

        close_connection = False
        try:
            if self._raw.closed:
                close_connection = True
            elif self._raw.get_transaction_status() != extensions.TRANSACTION_STATUS_IDLE:
                self._raw.rollback()
        except Exception:
            close_connection = True

        try:
            self._pool.putconn(self._raw, close=close_connection)
        except Exception:
            try:
                self._raw.close()
            except Exception:
                pass
        finally:
            self._slots.release()

    def __getattr__(self, name):
        return getattr(self._raw, name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type:
            try:
                self.rollback()
            except Exception:
                pass
        self.close()
        return False


def _get_pool():
    global _pool, _pool_pid, _pool_dsn, _pool_slots

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL environment variable is not configured.")

    pid = os.getpid()
    max_connections = max(2, int(os.getenv("DB_POOL_MAX", "6")))

    with _pool_lock:
        # Recreate after a Gunicorn fork or a DATABASE_URL change.
        if _pool is None or _pool_pid != pid or _pool_dsn != dsn:
            if _pool is not None and _pool_pid == pid:
                try:
                    _pool.closeall()
                except Exception:
                    pass

            connect_kwargs = {
                "dsn": dsn,
                "application_name": "ai_quiz_assessment",
            }
            # Avoid passing a libpq parameter twice when it is already in the URL.
            if "connect_timeout=" not in dsn:
                connect_kwargs["connect_timeout"] = max(3, int(os.getenv("DB_CONNECT_TIMEOUT", "10")))
            sslmode = os.getenv("DB_SSLMODE", "require")
            if sslmode and "sslmode=" not in dsn:
                connect_kwargs["sslmode"] = sslmode

            _pool = ThreadedConnectionPool(
                minconn=1,
                maxconn=max_connections,
                **connect_kwargs,
            )
            _pool_slots = threading.BoundedSemaphore(max_connections)
            _pool_pid = pid
            _pool_dsn = dsn

    return _pool, _pool_slots


def get_db_connection(cursor_factory=None):
    """Borrow a connection; close() returns it to the process pool."""
    pool, slots = _get_pool()
    wait_seconds = max(1, float(os.getenv("DB_POOL_WAIT_TIMEOUT", "12")))

    if not slots.acquire(timeout=wait_seconds):
        raise TimeoutError(
            "Database is busy. Please retry shortly; connection pool wait timed out."
        )

    try:
        raw = pool.getconn()
        if raw.closed:
            pool.putconn(raw, close=True)
            raw = pool.getconn()
        return PooledConnection(raw, pool, slots, cursor_factory)
    except Exception:
        slots.release()
        raise
