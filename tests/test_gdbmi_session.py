"""Integration tests for GdbMiSession driving the fake-gdb subprocess (no hardware)."""

from __future__ import annotations

import pytest

from alynprog.core.errors import GdbError, MiTimeoutError, SessionDied
from alynprog.transports.gdbmi import parse_memory_bytes


def test_echo_token_correlation(gdb_session_factory):
    session = gdb_session_factory()
    for _ in range(5):
        result = session.execute("-test-echo")
        assert result.message == "done"
        # fake echoes the exact token it received back to us.
        assert result.payload["echo"] == str(result.token)


def test_interleaved_async_records(gdb_session_factory):
    ticks: list[str] = []
    session = gdb_session_factory()
    session.on_async("tick", lambda _msg, payload: ticks.append(payload.get("n")))
    result = session.execute("-test-async 4")
    assert result.message == "done"
    assert ticks == ["0", "1", "2", "3"]


def test_timeout_then_session_still_usable(gdb_session_factory):
    session = gdb_session_factory()
    with pytest.raises(MiTimeoutError):
        session.execute("-test-slow 0.8", timeout=0.2)
    # The session must remain healthy and answer subsequent commands.
    result = session.execute("-test-echo", timeout=5.0)
    assert result.message == "done"


def test_error_result_raises_gdberror(gdb_session_factory):
    session = gdb_session_factory()
    with pytest.raises(GdbError) as excinfo:
        session.execute("-test-error boom")
    assert "boom" in str(excinfo.value)


def test_session_death_surfaces_as_sessiondied(gdb_session_factory):
    session = gdb_session_factory()
    with pytest.raises(SessionDied):
        session.execute("-test-die", timeout=5.0)
    assert not session.is_alive()
    # Further commands fail fast rather than hanging.
    with pytest.raises(SessionDied):
        session.execute("-test-echo")


def test_console_output_is_captured(gdb_session_factory):
    session = gdb_session_factory()
    text, result = session.console("info mem")
    assert result.message == "done"
    assert "0x08000000" in text
    assert "flash" in text


def test_memory_read_round_trips_through_session(gdb_session_factory):
    session = gdb_session_factory()
    result = session.execute("-data-read-memory-bytes 0x20000000 4")
    assert parse_memory_bytes(result.payload) == bytes([0x00, 0x01, 0x02, 0x03])


def test_log_callback_receives_streams(gdb_session_factory):
    logs: list[tuple[str, str]] = []
    session = gdb_session_factory(log_callback=lambda stream, text: logs.append((stream, text)))
    session.console("info mem")
    streams = {stream for stream, _ in logs}
    assert "command" in streams  # our outgoing commands are logged
    assert "console" in streams  # gdb console stream is logged


def test_stop_terminates_process(gdb_session_factory):
    session = gdb_session_factory()
    assert session.is_alive()
    session.stop()
    assert not session.is_alive()
