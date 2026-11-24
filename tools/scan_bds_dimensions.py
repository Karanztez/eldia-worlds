"""Scan Linux ELF64 or Windows PE64 bedrock_server binaries for CustomDimension & LevelStorage targets."""

from __future__ import annotations

import argparse
import re
import struct
from pathlib import Path


def scan_linux_elf(path: Path) -> None:
    """Locate DimensionManager, ServerLevel, DBStorage, and DimensionDataPacket targets in ELF64."""
    data = path.read_bytes()
    if data[:4] != b"\x7fELF":
        raise ValueError(f"{path} is not an ELF binary")

    e_shoff = struct.unpack_from("<Q", data, 40)[0]
    e_shentsize, e_shnum, _e_shstrndx = struct.unpack_from("<HHH", data, 58)
    sections = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        sh_name, sh_type, _flags, sh_addr, sh_offset, sh_size = struct.unpack_from(
            "<IIQQQQ", data, off
        )[:6]
        sections.append((sh_name, sh_type, sh_addr, sh_offset, sh_size))

    def off_to_va(raw_off: int) -> int | None:
        for _, sh_type, addr, off, size in sections:
            if sh_type != 8 and addr != 0 and off <= raw_off < off + size:
                return addr + (raw_off - off)
        return None

    sigs = [
        (
            "DimensionManager::registerCustomDimension",
            re.compile(
                rb"\x55\x41\x57\x41\x56\x41\x54\x53\x49\x89\xcc\x48\x89\xd3\x49\x89\xf6\x49\x89\xff\x48\x8b\x7f\x08\xe8"
            ),
        ),
        (
            "ServerLevel::getDimensionManager",
            re.compile(rb"\x48\x8b\x87..\x00\x00\x48\x8b\x40\x18\xc3", re.DOTALL),
        ),
        (
            "ServerLevel::getOrCreateDimension",
            re.compile(rb"\x48\x8b\x86..\x00\x00\x48\x8b\x70\x18\xe9", re.DOTALL),
        ),
    ]
    for label, pat in sigs:
        hits = [off_to_va(m.start()) or 0 for m in pat.finditer(data)]
        if hits:
            print(f"[OK] {label} -> {[hex(h) for h in hits[:4]]}")
        else:
            print(f"[MISS] {label}")

    for needle in (
        b"registerCustomDimension",
        b"_registerCustomDimensionWithFactory",
        b"DimensionDataPacket",
        b"11ServerLevel\x00",
        b"9DBStorage\x00",
        b"19DimensionDataPacket\x00",
    ):
        pos = data.find(needle)
        if pos != -1:
            text_label = needle.rstrip(b"\x00").decode("ascii", errors="replace")
            print(f"[STR] {text_label} at file=0x{pos:08x} (VA=0x{off_to_va(pos) or 0:08x})")


def scan_windows_pe(path: Path) -> None:
    """Locate CustomDimension, ServerLevel, and packet targets in Windows PE64 bedrock_server.exe."""
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise ValueError(f"{path} is not a PE binary")

    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    num_sections = struct.unpack_from("<H", data, pe_off + 6)[0]
    opt_hdr_size = struct.unpack_from("<H", data, pe_off + 20)[0]
    sec_table_off = pe_off + 24 + opt_hdr_size
    sections = []
    for i in range(num_sections):
        off = sec_table_off + i * 40
        vsize, vaddr, rsize, raddr = struct.unpack_from("<IIII", data, off + 8)
        sections.append((vaddr, vsize, raddr, rsize))

    def raw_to_rva(raw_off: int) -> int | None:
        for va, _vs, ra, rs in sections:
            if ra <= raw_off < ra + rs:
                return va + (raw_off - ra)
        return None

    win_dm = re.compile(rb"\x48\x8b\x81..\x00\x00\x48\x8b\x40\x18\xc3", re.DOTALL)
    dm_hits = [raw_to_rva(m.start()) or 0 for m in win_dm.finditer(data)]
    print(f"[{'OK' if dm_hits else 'MISS'}] ServerLevel::getDimensionManager (Win64) -> {[hex(h) for h in dm_hits[:4]]}")

    for needle in (
        b"registerCustomDimension",
        b"_registerCustomDimensionWithFactory@DimensionManager",
        b"DimensionDataPacket",
        b"DBChunkStorage::saveLiveChunk",
    ):
        pos = data.find(needle)
        if pos != -1:
            print(f"[STR] {needle.decode()} at file=0x{pos:08x} (RVA=0x{raw_to_rva(pos) or 0:08x})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan bedrock_server binary for CustomDimension symbols.")
    parser.add_argument("binary", type=Path, help="Path to bedrock_server (ELF) or bedrock_server.exe (PE)")
    args = parser.parse_args()

    magic = args.binary.read_bytes()[:4]
    if magic == b"\x7fELF":
        scan_linux_elf(args.binary)
    elif magic[:2] == b"MZ":
        scan_windows_pe(args.binary)
    else:
        raise SystemExit(f"Unsupported binary format: {args.binary}")


if __name__ == "__main__":
    main()
