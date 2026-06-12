"""A robust, Qt-free GDB/MI session driving an external GDB as a subprocess.

We deliberately use ``pygdbmi`` only for its line parser (``gdbmiparser.parse_response``) and own
everything else ourselves: the subprocess lifecycle, a continuous stdout reader thread, token-based
command/response correlation, and per-operation timeouts. ``pygdbmi``'s own ``GdbController`` is
avoided because it polls and hides the process lifecycle.

GDB/MI is strictly serial, so :class:`GdbMiSession` runs one command at a time: :meth:`execute`
sends a tokened command and blocks until the matching ``^result`` record arrives. Stream records
(``~`` console, ``&`` log, ``@`` target) and asynchronous records (``*stopped``, ``+download``,
``=…``) that arrive in the meantime are forwarded to the log callback / async handlers and also
attached to the in-flight command so callers can parse console output (e.g. ``compare-sections``).
"""

from __future__ import annotations

import contextlib
import re
import subprocess
import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from pygdbmi import gdbmiparser

from alynprog.core.errors import GdbError, MiTimeoutError, SessionDied

DEFAULT_TIMEOUT = 10.0

# Record types that pygdbmi assigns to the GDB/MI stream records.
_STREAM_TYPES = {"console", "log", "target", "output"}

# Callback signatures.
LogCallback = Callable[[str, str], None]  # (stream, text)
AsyncCallback = Callable[[str, Any], None]  # (message, payload)


@dataclass
class MiResult:
    """The outcome of a single GDB/MI command."""

    message: str  # result class: "done" | "running" | "connected" | "error" | "exit"
    payload: Any  # parsed result payload (usually a dict), or None
    token: int
    records: list[dict] = field(default_factory=list)  # stream/async records seen while in flight

    def console_text(self) -> str:
        """Concatenated textual output captured during the command.

        Includes both ``console`` (``~``) and ``target`` (``@``) stream records: native GDB
        commands like ``compare-sections`` and ``info mem`` emit console streams, while remote
        ``monitor`` output (e.g. from a Black Magic Probe) arrives as target streams.
        """
        return "".join(
            _as_text(r.get("payload"))
            for r in self.records
            if r.get("type") in ("console", "target")
        )


@dataclass
class _Pending:
    command: str
    token: int
    event: threading.Event = field(default_factory=threading.Event)
    result: MiResult | None = None
    error: Exception | None = None
    records: list[dict] = field(default_factory=list)


class GdbMiSession:
    """Owns a GDB subprocess and exposes a synchronous, tokened ``execute`` API."""

    def __init__(
        self,
        gdb_path: str,
        *,
        extra_args: tuple[str, ...] = (),
        log_callback: LogCallback | None = None,
        argv_override: list[str] | None = None,
    ) -> None:
        self._gdb_path = gdb_path
        self._extra_args = tuple(extra_args)
        self._log_callback = log_callback
        # argv_override lets tests launch a fake "gdb" (e.g. a Python script) without the standard
        # gdb flags being misinterpreted by the interpreter.
        self._argv_override = argv_override
        self._proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._token = 0
        self._pending: dict[int, _Pending] = {}
        self._active_token: int | None = None
        self._async_handlers: dict[str, list[AsyncCallback]] = defaultdict(list)
        self._alive = threading.Event()

    # --- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        if self._proc is not None:
            raise RuntimeError("session already started")
        argv = self._argv_override or [
            self._gdb_path,
            "-nx",
            "-q",
            "--interpreter=mi3",
            *self._extra_args,
        ]
        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise SessionDied(f"failed to launch GDB ({self._gdb_path!r}): {exc}") from exc
        self._alive.set()
        self._reader = threading.Thread(target=self._read_loop, name="gdbmi-reader", daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=self._read_stderr, name="gdbmi-stderr", daemon=True
        )
        self._stderr_reader.start()

    def is_alive(self) -> bool:
        return self._alive.is_set() and self._proc is not None and self._proc.poll() is None

    def stop(self, timeout: float = 2.0) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                try:
                    if proc.stdin is not None:
                        proc.stdin.write("-gdb-exit\n")
                        proc.stdin.flush()
                except OSError:
                    pass
                try:
                    proc.wait(timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        finally:
            self._alive.clear()
            self._fail_all_pending(SessionDied("session stopped"))

    # --- command API -----------------------------------------------------------

    def execute(self, command: str, timeout: float = DEFAULT_TIMEOUT) -> MiResult:
        """Send a GDB/MI *command*, block until its result record, and return it.

        Raises :class:`GdbError` on ``^error``, :class:`MiTimeoutError` on timeout, and
        :class:`SessionDied` if GDB exits.
        """
        pending = _Pending(command=command, token=0)
        with self._lock:
            if not self.is_alive() or self._proc is None or self._proc.stdin is None:
                raise SessionDied("gdb is not running")
            self._token += 1
            token = self._token
            pending.token = token
            self._pending[token] = pending
            self._active_token = token
            line = f"{token}{command}\n"
            try:
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
            except OSError as exc:
                self._pending.pop(token, None)
                self._active_token = None
                raise SessionDied(f"failed to write to gdb: {exc}") from exc
            self._emit_log("command", line.rstrip())

        completed = pending.event.wait(timeout)
        with self._lock:
            self._pending.pop(token, None)
            if self._active_token == token:
                self._active_token = None

        if not completed:
            raise MiTimeoutError(command, timeout)
        if pending.error is not None:
            raise pending.error
        result = pending.result
        assert result is not None
        if result.message == "error":
            raise GdbError(_error_message(result.payload), command=command)
        return result

    def console(self, text: str, timeout: float = DEFAULT_TIMEOUT) -> tuple[str, MiResult]:
        """Run a console command via ``-interpreter-exec``; return its console output and result."""
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        result = self.execute(f'-interpreter-exec console "{escaped}"', timeout)
        return result.console_text(), result

    # --- async handlers --------------------------------------------------------

    def on_async(self, message: str, callback: AsyncCallback) -> None:
        """Register *callback* for async records whose class is *message* (``"*"`` for all)."""
        with self._lock:
            self._async_handlers[message].append(callback)

    def remove_async(self, message: str, callback: AsyncCallback) -> None:
        with self._lock:
            handlers = self._async_handlers.get(message)
            if handlers and callback in handlers:
                handlers.remove(callback)

    # --- reader threads --------------------------------------------------------

    def _read_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            for raw in proc.stdout:
                line = raw.rstrip("\r\n")
                if not line or line.startswith("(gdb)"):
                    continue
                try:
                    record = gdbmiparser.parse_response(line)
                except Exception:
                    self._emit_log("log", line)
                    continue
                self._dispatch(record)
        finally:
            self._alive.clear()
            self._fail_all_pending(SessionDied("gdb stdout closed"))

    def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for raw in proc.stderr:
            text = raw.rstrip("\r\n")
            if text:
                self._emit_log("stderr", text)

    def _dispatch(self, record: dict) -> None:
        rtype = record.get("type")
        if rtype == "result":
            self._complete(record)
            return
        if rtype in _STREAM_TYPES:
            text = _as_text(record.get("payload"))
            # pygdbmi does not structure status-async ("+…") records: it reports them as plain
            # "output" with the raw line as payload. Re-route them so progress handlers see them.
            if rtype == "output" and text.startswith("+"):
                async_message, async_payload = _parse_status_async(text)
                if async_message:
                    self._attach_to_active(record)
                    self._dispatch_async(async_message, async_payload)
                    return
            self._emit_log(str(rtype), text)
            self._attach_to_active(record)
            return
        message = record.get("message")
        if message:
            self._attach_to_active(record)
            self._dispatch_async(str(message), record.get("payload"))

    def _complete(self, record: dict) -> None:
        token = record.get("token")
        with self._lock:
            pending = self._pending.get(token) if token is not None else None
            if pending is None:
                return
            pending.result = MiResult(
                message=str(record.get("message")),
                payload=record.get("payload"),
                token=pending.token,
                records=pending.records,
            )
        pending.event.set()

    def _attach_to_active(self, record: dict) -> None:
        with self._lock:
            token = self._active_token
            pending = self._pending.get(token) if token is not None else None
            if pending is not None:
                pending.records.append(record)

    def _dispatch_async(self, message: str, payload: Any) -> None:
        with self._lock:
            handlers = list(self._async_handlers.get(message, ()))
            handlers += list(self._async_handlers.get("*", ()))
        for callback in handlers:
            # A buggy handler must not break the reader thread.
            with contextlib.suppress(Exception):
                callback(message, payload)

    def _fail_all_pending(self, error: Exception) -> None:
        with self._lock:
            pendings = list(self._pending.values())
            self._pending.clear()
            self._active_token = None
        for pending in pendings:
            pending.error = error
            pending.event.set()

    def _emit_log(self, stream: str, text: str) -> None:
        if self._log_callback is not None and text:
            # Logging must never crash the reader thread.
            with contextlib.suppress(Exception):
                self._log_callback(stream, text)


# --- helpers ------------------------------------------------------------------


def _as_text(payload: Any) -> str:
    return payload if isinstance(payload, str) else ""


_ASYNC_PAIR_RE = re.compile(r'([A-Za-z][\w-]*)="([^"]*)"')


def _parse_status_async(text: str) -> tuple[str, dict[str, str]]:
    """Parse a raw ``+class,key="value",…`` status-async line into (class, fields)."""
    body = text[1:]  # drop leading "+"
    cls, _, rest = body.partition(",")
    fields = dict(_ASYNC_PAIR_RE.findall(rest))
    return cls.strip(), fields


def _error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        msg = payload.get("msg")
        if isinstance(msg, str):
            return msg
    return str(payload) if payload is not None else "unknown GDB error"


# --- pure parse helpers (unit-testable without a subprocess) -------------------


class RawMemRegion(NamedTuple):
    start: int
    size: int
    kind: str  # "flash" | "ram" | "unknown"
    blocksize: int = 0  # flash erase-block (sector) size, 0 if unknown/not flash


class CompareSection(NamedTuple):
    name: str
    matched: bool


class DownloadProgress(NamedTuple):
    section: str | None
    total_sent: int | None
    total_size: int | None


def parse_memory_bytes(payload: Any) -> bytes:
    """Extract bytes from a ``-data-read-memory-bytes`` result payload."""
    if not isinstance(payload, dict):
        return b""
    chunks = payload.get("memory")
    if not isinstance(chunks, list):
        return b""
    # Order by offset so out-of-order chunks reassemble correctly.
    ordered = sorted(chunks, key=lambda c: _to_int(c.get("offset"), 0))
    out = bytearray()
    for chunk in ordered:
        contents = chunk.get("contents")
        if isinstance(contents, str):
            with contextlib.suppress(ValueError):
                out += bytes.fromhex(contents)
    return bytes(out)


def parse_register_names(payload: Any) -> dict[int, str]:
    """Map register index -> name from ``-data-list-register-names`` (skips empty slots)."""
    names: dict[int, str] = {}
    if isinstance(payload, dict):
        raw = payload.get("register-names")
        if isinstance(raw, list):
            for index, name in enumerate(raw):
                if isinstance(name, str) and name:
                    names[index] = name
    return names


def parse_register_values(payload: Any) -> dict[int, str]:
    """Map register index -> value string from ``-data-list-register-values``."""
    values: dict[int, str] = {}
    if isinstance(payload, dict):
        raw = payload.get("register-values")
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    number = _to_int(item.get("number"), -1)
                    value = item.get("value")
                    if number >= 0 and isinstance(value, str):
                        values[number] = value
    return values


def parse_download_progress(payload: Any) -> DownloadProgress:
    """Extract overall progress from a ``+download`` async record payload."""
    if not isinstance(payload, dict):
        return DownloadProgress(None, None, None)
    return DownloadProgress(
        section=payload.get("section"),
        total_sent=_opt_int(payload.get("total-sent")),
        total_size=_opt_int(payload.get("total-size")),
    )


def parse_compare_sections(text: str) -> list[CompareSection]:
    """Parse the console output of GDB's ``compare-sections`` command."""
    sections: list[CompareSection] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("Section "):
            continue
        # "Section .text, range 0x... -- 0x...: matched." / ": MIS-MATCHED!"
        name = stripped[len("Section ") :].split(",", 1)[0].strip()
        matched = "MIS-MATCHED" not in stripped.upper()
        sections.append(CompareSection(name=name, matched=matched))
    return sections


def parse_info_mem(text: str) -> list[RawMemRegion]:
    """Parse the console output of GDB's ``info mem`` into memory regions."""
    regions: list[RawMemRegion] = []
    for line in text.splitlines():
        parts = line.split()
        # Expected: "<num> <enb> <low> <high> <attrs...>"; require two hex addresses.
        if len(parts) < 4:
            continue
        low = _maybe_hex(parts[2])
        high = _maybe_hex(parts[3])
        if low is None or high is None or high <= low:
            continue
        tail = parts[4:]
        attrs = " ".join(tail).lower()
        regions.append(
            RawMemRegion(
                start=low,
                size=high - low,
                kind=_classify_attrs(attrs),
                blocksize=_parse_blocksize(tail),
            )
        )
    return regions


def _parse_blocksize(tokens: list[str]) -> int:
    """Extract the flash erase-block size from ``info mem`` attrs (``blocksize 0x400`` -> 0x400)."""
    for i, token in enumerate(tokens):
        if token.lower() == "blocksize" and i + 1 < len(tokens):
            value = _maybe_hex(tokens[i + 1])
            if value is not None:
                return value
    return 0


def _classify_attrs(attrs: str) -> str:
    if "flash" in attrs:
        return "flash"
    if "rw" in attrs or "ram" in attrs:
        return "ram"
    if "ro" in attrs or "rom" in attrs:
        return "flash"
    return "unknown"


def _maybe_hex(token: str) -> int | None:
    token = token.strip()
    if token.lower().startswith("0x"):
        try:
            return int(token, 16)
        except ValueError:
            return None
    return None


def _to_int(value: Any, default: int) -> int:
    result = _opt_int(value)
    return result if result is not None else default


def _opt_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        token = value.strip()
        try:
            return int(token, 16) if token.lower().startswith("0x") else int(token)
        except ValueError:
            return None
    return None
