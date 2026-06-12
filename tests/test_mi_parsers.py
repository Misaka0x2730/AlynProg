"""Unit tests for the pure GDB/MI parse helpers (no subprocess, no Qt)."""

from __future__ import annotations

from alynprog.transports.gdbmi import (
    _parse_status_async,
    parse_compare_sections,
    parse_download_progress,
    parse_info_mem,
    parse_memory_bytes,
    parse_register_names,
    parse_register_values,
)


def test_parse_memory_bytes_single_chunk():
    payload = {"memory": [{"begin": "0x20000000", "offset": "0x0", "contents": "01020304"}]}
    assert parse_memory_bytes(payload) == b"\x01\x02\x03\x04"


def test_parse_memory_bytes_orders_by_offset():
    payload = {
        "memory": [
            {"offset": "0x2", "contents": "cccc"},
            {"offset": "0x0", "contents": "aabb"},
        ]
    }
    assert parse_memory_bytes(payload) == b"\xaa\xbb\xcc\xcc"


def test_parse_memory_bytes_handles_garbage():
    assert parse_memory_bytes(None) == b""
    assert parse_memory_bytes({"memory": "nope"}) == b""


def test_parse_register_names_skips_empty_slots():
    payload = {"register-names": ["r0", "r1", "", "pc"]}
    assert parse_register_names(payload) == {0: "r0", 1: "r1", 3: "pc"}


def test_parse_register_values():
    payload = {
        "register-values": [{"number": "0", "value": "0x1"}, {"number": "15", "value": "0x8000000"}]
    }
    assert parse_register_values(payload) == {0: "0x1", 15: "0x8000000"}


def test_parse_status_async_download():
    raw = '+download,{section=".text",section-sent="512",total-sent="512",total-size="4096"}'
    cls, fields = _parse_status_async(raw)
    assert cls == "download"
    assert fields["section"] == ".text"
    assert fields["total-sent"] == "512"
    assert fields["total-size"] == "4096"


def test_parse_download_progress():
    fields = {"section": ".text", "total-sent": "512", "total-size": "4096"}
    progress = parse_download_progress(fields)
    assert progress.section == ".text"
    assert progress.total_sent == 512
    assert progress.total_size == 4096


def test_parse_download_progress_partial():
    progress = parse_download_progress({"section": ".text", "total-size": "4096"})
    assert progress.total_sent is None
    assert progress.total_size == 4096


def test_parse_compare_sections_matched_and_mismatched():
    text = (
        "Section .text, range 0x08000000 -- 0x08000400: matched.\n"
        "Section .data, range 0x20000000 -- 0x20000100: MIS-MATCHED!\n"
    )
    sections = parse_compare_sections(text)
    assert sections[0].name == ".text"
    assert sections[0].matched is True
    assert sections[1].name == ".data"
    assert sections[1].matched is False


def test_parse_info_mem_classifies_regions():
    text = (
        "Using memory regions provided by the target.\n"
        "Num Enb Low Addr   High Addr  Attrs\n"
        "0   y   0x08000000 0x08100000 flash blocksize 0x800 nocache\n"
        "1   y   0x20000000 0x20020000 rw nocache\n"
    )
    regions = parse_info_mem(text)
    assert len(regions) == 2
    assert regions[0].start == 0x0800_0000
    assert regions[0].size == 0x0010_0000
    assert regions[0].kind == "flash"
    assert regions[0].blocksize == 0x800
    assert regions[1].start == 0x2000_0000
    assert regions[1].kind == "ram"
    assert regions[1].blocksize == 0


def test_parse_info_mem_with_tabs_and_blocksize():
    # Real BMP output uses a tab before the address column.
    text = "0   y  \t0x08000000 0x08008000 flash blocksize 0x400 nocache"
    regions = parse_info_mem(text)
    assert len(regions) == 1
    assert regions[0].start == 0x0800_0000
    assert regions[0].size == 0x8000
    assert regions[0].blocksize == 0x400


def test_parse_info_mem_ignores_headers_and_bad_lines():
    assert parse_info_mem("garbage\nNo. Att Driver\n") == []
