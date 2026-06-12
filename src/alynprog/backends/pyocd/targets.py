"""pyOCD target catalog: fetch the target database, model it, and cache it on disk.

The list of flashable targets is produced by pyOCD itself (``ListGenerator.list_targets()``), never
hardcoded. It is ~400 entries (builtin + installed CMSIS packs) and costs a one-time ~2 s pyocd
import plus ~0.2 s per call, so it is cached to disk and only refetched when pyOCD's version or the
set of installed CMSIS packs changes.

Fetching builds throwaway ``StubProbe`` sessions inside pyOCD, which clobber
``Session.get_current()`` — so :meth:`TargetCatalog.fetch` must run only while no probe session is
open (i.e. while DISCONNECTED). See docs/pyocd-spike.md.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from alynprog.backends.pyocd._api import PyocdApi

# Bump when the on-disk cache layout changes; an older/newer schema is treated as stale.
_CACHE_SCHEMA = 1


@dataclass(frozen=True)
class TargetRecord:
    """One selectable pyOCD target, as shown in the target-picker dialog."""

    name: str  # pyOCD target id, e.g. "stm32f103rc"; becomes ConnectOptions.target_name
    vendor: str
    part_number: str
    families: tuple[str, ...]
    source: str  # "builtin" | "pack" | "cbuild-run"

    @classmethod
    def from_pyocd(cls, entry: dict[str, Any]) -> TargetRecord:
        """Build from a ``ListGenerator.list_targets()`` entry (key ``part_families``)."""
        return cls(
            name=str(entry.get("name", "")),
            vendor=str(entry.get("vendor", "")),
            part_number=str(entry.get("part_number", "")),
            families=_as_str_tuple(entry.get("part_families")),
            source=str(entry.get("source", "")),
        )

    @classmethod
    def from_cache(cls, entry: dict[str, Any]) -> TargetRecord:
        """Build from a cache entry (key ``families``)."""
        return cls(
            name=str(entry.get("name", "")),
            vendor=str(entry.get("vendor", "")),
            part_number=str(entry.get("part_number", "")),
            families=_as_str_tuple(entry.get("families")),
            source=str(entry.get("source", "")),
        )

    def to_cache(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "vendor": self.vendor,
            "part_number": self.part_number,
            "families": list(self.families),
            "source": self.source,
        }


class TargetCatalog:
    """Disk-cached view of pyOCD's target database.

    Pure and Qt-free: the cache path is injected so this is unit-testable without a QApplication.
    Production code resolves the path via :func:`default_cache_path`.
    """

    def __init__(self, cache_path: Path) -> None:
        self._cache_path = Path(cache_path)

    @property
    def cache_path(self) -> Path:
        return self._cache_path

    def load_cached(self) -> list[TargetRecord] | None:
        """Return cached records, or ``None`` if there is no readable cache."""
        data = self._read_cache()
        if data is None:
            return None
        return [TargetRecord.from_cache(e) for e in data.get("targets", [])]

    def is_stale(self) -> bool:
        """True if the cache is missing, the wrong schema, or pyOCD/its packs have changed."""
        data = self._read_cache()
        if data is None or data.get("schema") != _CACHE_SCHEMA:
            return True
        if data.get("pyocd_version") != _installed_pyocd_version():
            return True
        stored_path = data.get("pack_data_path")
        stored_fingerprint = data.get("pack_fingerprint")
        if stored_path and stored_fingerprint is not None:
            current = _pack_fingerprint(Path(stored_path))
            return current != stored_fingerprint
        return False

    def fetch(self, api: PyocdApi, *, pack_data_path: Path | None = None) -> list[TargetRecord]:
        """Fetch the live target list from pyOCD and (re)write the cache. DISCONNECTED-only.

        *pack_data_path* overrides CMSIS-pack directory resolution (used by tests); production
        passes ``None`` and the directory is resolved via cmsis-pack-manager.
        """
        result = api.list_targets()
        records = [TargetRecord.from_pyocd(t) for t in result.get("targets", [])]
        version = str(result.get("pyocd_version") or _installed_pyocd_version() or "")
        data_path = pack_data_path if pack_data_path is not None else _resolve_pack_data_path()
        payload = {
            "schema": _CACHE_SCHEMA,
            "pyocd_version": version,
            "pack_data_path": str(data_path) if data_path is not None else "",
            "pack_fingerprint": _pack_fingerprint(data_path) if data_path is not None else None,
            "targets": [r.to_cache() for r in records],
        }
        self._write_cache(payload)
        return records

    def _read_cache(self) -> dict[str, Any] | None:
        try:
            raw = self._cache_path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _write_cache(self, payload: dict[str, Any]) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a crash mid-write never leaves a truncated cache.
        tmp = self._cache_path.with_name(self._cache_path.name + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self._cache_path)


def default_cache_path() -> Path:
    """Production cache location under the app's local-data dir (needs Qt app metadata set)."""
    from PySide6.QtCore import QStandardPaths

    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return Path(base) / "pyocd_targets_cache.json"


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(str(v) for v in value)


def _installed_pyocd_version() -> str | None:
    try:
        return importlib.metadata.version("pyocd")
    except importlib.metadata.PackageNotFoundError:
        return None


def _pack_fingerprint(data_path: Path) -> str:
    """A cheap content fingerprint of the CMSIS-pack dir: index.json + installed ``*.pack`` files.

    Catches ``pyocd pack update`` (index.json mtime) and ``pack install``/``clean`` (pack files),
    without importing pyocd or cmsis-pack-manager — so :meth:`TargetCatalog.is_stale` stays light.
    """
    entries: list[tuple[str, int, int]] = []
    try:
        if data_path.is_dir():
            candidates: list[Path] = []
            index = data_path / "index.json"
            if index.is_file():
                candidates.append(index)
            candidates.extend(sorted(data_path.rglob("*.pack")))
            for path in candidates:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                rel = path.relative_to(data_path).as_posix()
                entries.append((rel, stat.st_mtime_ns, stat.st_size))
    except OSError:
        pass
    raw = json.dumps(sorted(entries), separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _resolve_pack_data_path() -> Path | None:
    try:
        import cmsis_pack_manager

        return Path(cmsis_pack_manager.Cache(True, True).data_path)
    except Exception:
        # cmsis-pack-manager missing or unhappy: skip pack fingerprinting, fall back to
        # version-based invalidation plus the dialog's manual Refresh.
        return None
