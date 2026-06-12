"""Minimal ELF program-header reader: extract loadable segments by their load address.

Used by the firmware-document layer to open an ELF for viewing and editing. We read only the
program headers and keep each ``PT_LOAD`` entry's *load* address (``p_paddr``, the LMA — where the
bytes physically live, e.g. an initialised ``.data`` section stored in flash even though it runs
from RAM) together with its ``p_filesz`` bytes from the file. Segments that carry no file bytes
(``.bss``-style, ``p_filesz == 0``) are dropped. This is independent of the flashing path, which
hands ELF files straight to the backend, so parsing quirks here never affect programming.
"""

from __future__ import annotations

import struct
from pathlib import Path

from alynprog.core.errors import ImageError
from alynprog.core.image import Segment

_ELF_MAGIC = b"\x7fELF"
_PT_LOAD = 1
_EI_CLASS = 4  # byte index: 1 = 32-bit, 2 = 64-bit
_EI_DATA = 5  # byte index: 1 = little-endian, 2 = big-endian


def load_elf_segments(path: str | Path) -> list[Segment]:
    """Return the loadable (``PT_LOAD``) segments of an ELF file, sorted by load address.

    Each segment's address is its load address (``p_paddr`` if non-zero, otherwise ``p_vaddr``) and
    its data is the ``p_filesz`` bytes stored in the file. Zero-length (``.bss``) segments are
    skipped. Raises :class:`ImageError` on a malformed or unsupported ELF.
    """
    path = Path(path)
    data = path.read_bytes()
    if len(data) < 16 or data[:4] != _ELF_MAGIC:
        raise ImageError(f"not an ELF file: {path.name}")

    ei_class = data[_EI_CLASS]
    if ei_class not in (1, 2):
        raise ImageError(f"unsupported ELF class in {path.name}")
    is_64 = ei_class == 2
    endian = "<" if data[_EI_DATA] == 1 else ">"

    try:
        if is_64:
            e_phoff = struct.unpack_from(endian + "Q", data, 32)[0]
            e_phentsize, e_phnum = struct.unpack_from(endian + "HH", data, 54)
        else:
            e_phoff = struct.unpack_from(endian + "I", data, 28)[0]
            e_phentsize, e_phnum = struct.unpack_from(endian + "HH", data, 42)
    except struct.error as exc:
        raise ImageError(f"malformed ELF header in {path.name}: {exc}") from exc

    if e_phoff == 0 or e_phnum == 0:
        raise ImageError(f"ELF file has no program headers: {path.name}")

    segments: list[Segment] = []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        try:
            if is_64:
                # Elf64_Phdr: type, flags, offset, vaddr, paddr, filesz (memsz/align ignored).
                p_type, _flags, p_offset, p_vaddr, p_paddr, p_filesz = struct.unpack_from(
                    endian + "IIQQQQ", data, off
                )
            else:
                # Elf32_Phdr: type, offset, vaddr, paddr, filesz, memsz, flags (align ignored).
                p_type, p_offset, p_vaddr, p_paddr, p_filesz, _memsz, _flags = struct.unpack_from(
                    endian + "IIIIIII", data, off
                )
        except struct.error as exc:
            raise ImageError(f"malformed ELF program header in {path.name}: {exc}") from exc
        if p_type != _PT_LOAD or p_filesz == 0:
            continue
        chunk = data[p_offset : p_offset + p_filesz]
        if len(chunk) != p_filesz:
            raise ImageError(f"ELF segment runs past the end of {path.name}")
        segments.append(Segment(p_paddr or p_vaddr, chunk))

    if not segments:
        raise ImageError(f"ELF file has no loadable segments: {path.name}")
    segments.sort(key=lambda seg: seg.addr)
    return segments
