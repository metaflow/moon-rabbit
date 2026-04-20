"""Tests for DB.cursor() reconnect-on-failure resilience (fix for issue A)."""

import asyncio
from unittest.mock import MagicMock, patch

import psycopg2
import pytest

import main
from storage import DB


def make_db(mock_conn: MagicMock) -> DB:
    """Construct a DB instance with a pre-built mock connection."""
    with patch("storage.psycopg2.connect", return_value=mock_conn):
        db = DB("postgresql://fake/db")
    return db


def _open_conn() -> MagicMock:
    conn = MagicMock()
    conn.closed = 0
    return conn


# ---------------------------------------------------------------------------
# cursor() — happy path
# ---------------------------------------------------------------------------


def test_cursor_returns_on_healthy_connection():
    conn = _open_conn()
    db = make_db(conn)

    cur = db.cursor()

    conn.cursor.assert_called_once()
    cur.execute.assert_called_once_with("SELECT 1")


# ---------------------------------------------------------------------------
# cursor() — reconnect when conn.closed is set
# ---------------------------------------------------------------------------


def test_cursor_reconnects_when_conn_closed():
    conn = _open_conn()
    conn.closed = 1  # explicitly closed
    new_conn = _open_conn()

    db = make_db(conn)

    with patch("storage.psycopg2.connect", return_value=new_conn) as mock_connect:
        db.cursor()

    mock_connect.assert_called_once_with("postgresql://fake/db")
    assert db.conn is new_conn


# ---------------------------------------------------------------------------
# cursor() — reconnect on OperationalError (silently dropped SSL connection)
# ---------------------------------------------------------------------------


def test_cursor_reconnects_on_operational_error():
    conn = _open_conn()
    # Simulate a cursor whose execute raises OperationalError (dropped SSL)
    bad_cur = MagicMock()
    bad_cur.execute.side_effect = psycopg2.OperationalError("SSL connection closed")
    conn.cursor.return_value = bad_cur

    new_conn = _open_conn()
    db = make_db(conn)

    with patch("storage.psycopg2.connect", return_value=new_conn) as mock_connect:
        result = db.cursor()

    mock_connect.assert_called_once()
    assert db.conn is new_conn
    # After reconnect, cursor() on the new connection is returned
    new_conn.cursor.assert_called_once()
    assert result is new_conn.cursor.return_value


# ---------------------------------------------------------------------------
# expireVariables() — loop survives individual exceptions
# ---------------------------------------------------------------------------


def test_expire_variables_loop_survives_exception():
    sleep_count = 0
    to_thread_count = 0

    async def fake_to_thread(fn, *args, **kwargs):
        nonlocal to_thread_count
        to_thread_count += 1
        if to_thread_count == 1:
            raise Exception("DB blew up")

    async def fake_sleep(delay):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 2:
            raise asyncio.CancelledError

    async def run():
        with (
            patch("main.asyncio.to_thread", side_effect=fake_to_thread),
            patch("main.asyncio.sleep", side_effect=fake_sleep),
            patch("main.db") as mock_db,
        ):
            mock_db.return_value.expire_variables = MagicMock()
            mock_db.return_value.expire_old_queries = MagicMock()
            with pytest.raises(asyncio.CancelledError):
                await main.expireVariables()

    asyncio.run(run())

    # Two sleep cycles completed — loop survived the first exception
    assert sleep_count == 2
