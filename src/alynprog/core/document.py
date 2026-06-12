"""A mutable, addressed firmware buffer backed by a file on disk.

Where :class:`~alynprog.core.image.FirmwareImage` is a read-only view used to *flash* a file, a
:class:`FirmwareDocument` is what an editor tab edits: it holds one or more address-tagged segments
as mutable buffers, tracks a dirty flag, answers paged reads for the hex model, accepts byte
writes, and saves back to disk. BIN and Intel-HEX documents save in place; an ELF is editable in
memory but has no in-place save (it can only be exported with :meth:`save_as` to HEX or BIN).

A raw ``.bin`` has no addresses of its own, so it opens at offset ``0`` and is shown as a flat
file-offset view; a start address is only needed when comparing it against device memory, which the
UI handles by rebasing the segment at compare time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from intelhex import IntelHex

from alynprog.core.backend import MemoryRegion, RegionKind
from alynprog.core.elf import load_elf_segments
from alynprog.core.errors import ImageError
from alynprog.core.image import FirmwareImage, ImageKind

_PAD_BYTE = 0xFF  # fills page reads outside any segment; never shown (the model clips to regions)


@dataclass
class _Segment:
    addr: int
    data: bytearray

    @property
    def end(self) -> int:
        return self.addr + len(self.data)


def kind_for_suffix(suffix: str) -> ImageKind:
    """Map a filename suffix to an :class:`ImageKind`, defaulting to BIN for anything unknown."""
    ext = suffix.lower()
    if ext in (".hex", ".ihex", ".ihx"):
        return ImageKind.HEX
    if ext == ".elf":
        return ImageKind.ELF
    return ImageKind.BIN


class FirmwareDocument:
    def __init__(self, path: Path, kind: ImageKind, segments: list[_Segment]) -> None:
        self.path = path
        self.kind = kind
        self._segments = segments
        self._dirty = False

    # --- construction ----------------------------------------------------------

    @classmethod
    def open(cls, path: str | Path) -> FirmwareDocument:
        path = Path(path)
        if not path.is_file():
            raise ImageError(f"file not found: {path}")
        kind = FirmwareImage._detect_kind(path)
        if kind is ImageKind.ELF:
            segments = [_Segment(seg.addr, bytearray(seg.data)) for seg in load_elf_segments(path)]
        elif kind is ImageKind.HEX:
            image = FirmwareImage.load(path)
            segments = [_Segment(seg.addr, bytearray(seg.data)) for seg in image.segments]
        else:  # BIN: a flat file-offset view starting at 0.
            segments = [_Segment(0, bytearray(path.read_bytes()))]
        return cls(path, kind, segments)

    # --- queries ---------------------------------------------------------------

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def supports_in_place_save(self) -> bool:
        """Whether :meth:`save` can write this document back to its own file (ELF cannot)."""
        return self.kind is not ImageKind.ELF

    @property
    def total_size(self) -> int:
        return sum(len(seg.data) for seg in self._segments)

    def regions(self) -> list[MemoryRegion]:
        """One region per segment, for the hex view's region picker."""
        single = len(self._segments) == 1
        out: list[MemoryRegion] = []
        for index, seg in enumerate(self._segments):
            name = self.path.name if single else f"Segment {index}"
            out.append(MemoryRegion(seg.addr, len(seg.data), RegionKind.UNKNOWN, name=name))
        return out

    def segment_views(self) -> list[tuple[int, bytes]]:
        """An (address, bytes) snapshot of every segment, for comparing against device memory."""
        return [(seg.addr, bytes(seg.data)) for seg in self._segments]

    # --- reads / writes --------------------------------------------------------

    def read(self, base: int, size: int) -> bytes:
        """Return *size* bytes starting at *base*, padding any gaps outside the segments.

        The padding is never displayed: the hex model only addresses bytes inside a region, and the
        padding only ever falls in the part of a page that straddles a segment boundary.
        """
        out = bytearray([_PAD_BYTE]) * size
        for seg in self._segments:
            lo = max(base, seg.addr)
            hi = min(base + size, seg.end)
            if lo < hi:
                out[lo - base : hi - base] = seg.data[lo - seg.addr : hi - seg.addr]
        return bytes(out)

    def write(self, addr: int, data: bytes) -> bool:
        """Patch *data* at *addr*. Returns ``False`` if it does not fall inside a single segment."""
        end = addr + len(data)
        for seg in self._segments:
            if seg.addr <= addr and end <= seg.end:
                seg.data[addr - seg.addr : end - seg.addr] = data
                self._dirty = True
                return True
        return False

    # --- saving ----------------------------------------------------------------

    def save(self) -> None:
        if not self.supports_in_place_save:
            raise ImageError("this file format cannot be saved in place; use Save As to export it")
        self._write_file(self.path, self.kind)
        self._dirty = False

    def save_as(self, path: str | Path, kind: ImageKind | None = None) -> None:
        path = Path(path)
        kind = kind or kind_for_suffix(path.suffix)
        self._write_file(path, kind)
        self.path = path
        self.kind = kind
        self._dirty = False

    def _write_file(self, path: Path, kind: ImageKind) -> None:
        if kind is ImageKind.ELF:
            raise ImageError("exporting to ELF is not supported")
        if kind is ImageKind.BIN:
            if len(self._segments) != 1:
                raise ImageError(
                    "a .bin file can only hold a single contiguous segment; export to .hex instead"
                )
            path.write_bytes(bytes(self._segments[0].data))
            return
        ih = IntelHex()
        for seg in self._segments:
            ih.puts(seg.addr, bytes(seg.data))
        ih.write_hex_file(str(path))
