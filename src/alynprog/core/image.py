"""Firmware image loading: ELF / Intel-HEX / raw-BIN, plus BIN→HEX conversion for flashing.

GDB can ``load`` ELF and (BFD-permitting) Intel-HEX files directly. A raw ``.bin`` carries no
address information and cannot be flashed via GDB's ``restore … binary``, so we convert it to Intel
HEX at a user-supplied base address using the ``intelhex`` package and hand GDB the converted file.

For ELF we do not parse program headers in v1 — GDB owns the load and reports progress and total
size — so ``segments`` is empty and ``address_range`` is ``None`` for ELF images.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from intelhex import HexRecordError, IntelHex

from alynprog.core.errors import ImageError

_ELF_MAGIC = b"\x7fELF"


class ImageKind(StrEnum):
    ELF = "elf"
    HEX = "hex"
    BIN = "bin"


@dataclass(frozen=True)
class Segment:
    addr: int
    data: bytes

    @property
    def end(self) -> int:
        return self.addr + len(self.data)


class FirmwareImage:
    """A loaded firmware image ready to be flashed."""

    def __init__(
        self,
        path: Path,
        kind: ImageKind,
        segments: list[Segment],
        *,
        base_addr: int | None = None,
    ) -> None:
        self.path = path
        self.kind = kind
        self.segments = segments
        self.base_addr = base_addr

    # --- construction ----------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path, base_addr: int | None = None) -> FirmwareImage:
        path = Path(path)
        if not path.is_file():
            raise ImageError(f"file not found: {path}")
        kind = cls._detect_kind(path)
        if kind is ImageKind.ELF:
            return cls(path, kind, segments=[])
        if kind is ImageKind.HEX:
            return cls(path, kind, segments=cls._segments_from_hex(path))
        # BIN
        if base_addr is None:
            raise ImageError("a base address is required to load a raw .bin image")
        data = path.read_bytes()
        return cls(path, kind, segments=[Segment(base_addr, data)], base_addr=base_addr)

    @staticmethod
    def _detect_kind(path: Path) -> ImageKind:
        ext = path.suffix.lower()
        if ext == ".elf":
            return ImageKind.ELF
        if ext in (".hex", ".ihex", ".ihx"):
            return ImageKind.HEX
        if ext == ".bin":
            return ImageKind.BIN
        # No decisive extension: sniff content.
        head = path.read_bytes()[:16]
        if head.startswith(_ELF_MAGIC):
            return ImageKind.ELF
        if head[:1] == b":" and _looks_like_hex(path):
            return ImageKind.HEX
        return ImageKind.BIN

    @staticmethod
    def _segments_from_hex(path: Path) -> list[Segment]:
        try:
            ih = IntelHex(str(path))
        except (HexRecordError, ValueError) as exc:
            raise ImageError(f"invalid Intel-HEX file {path.name}: {exc}") from exc
        segments: list[Segment] = []
        for start, end in ih.segments():
            segments.append(Segment(start, ih.tobinstr(start=start, end=end - 1)))
        return segments

    # --- queries ---------------------------------------------------------------

    @property
    def total_size(self) -> int:
        return sum(len(seg.data) for seg in self.segments)

    def address_range(self) -> tuple[int, int] | None:
        """Return ``(min_addr, max_addr_exclusive)`` across segments, or ``None`` if unknown."""
        if not self.segments:
            return None
        start = min(seg.addr for seg in self.segments)
        end = max(seg.end for seg in self.segments)
        return start, end

    # --- flashing support ------------------------------------------------------

    def to_intel_hex(self) -> IntelHex:
        ih = IntelHex()
        for seg in self.segments:
            ih.puts(seg.addr, seg.data)
        return ih

    def loadable_path(self, workdir: Path) -> Path:
        """Return a path GDB can ``load``.

        ELF and HEX files are loaded as-is; a raw BIN is converted to an Intel-HEX file written
        into *workdir* and that path is returned.
        """
        if self.kind in (ImageKind.ELF, ImageKind.HEX):
            return self.path
        out = workdir / (self.path.stem + ".hex")
        self.to_intel_hex().write_hex_file(str(out))
        return out


def _looks_like_hex(path: Path) -> bool:
    try:
        with path.open("r", encoding="ascii") as fh:
            for _ in range(8):
                line = fh.readline()
                if not line:
                    break
                if not line.startswith(":"):
                    return False
        return True
    except (UnicodeDecodeError, OSError):
        return False
