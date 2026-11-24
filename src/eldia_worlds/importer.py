"""Pure-Python Bedrock LevelDB world importer for `eldia-worlds` (`import/` folder)."""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path
from typing import Any

_LEVELDB_FOOTER_MAGIC = bytes.fromhex("57fb808b247547db")
_SUBCHUNK_TAG = 0x2F


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    shift = 0
    val = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, pos
        shift += 7
    raise ValueError("Truncated varint")


def _read_sstable_block(data: bytes, offset: int, size: int) -> bytes:
    raw = data[offset : offset + size]
    comp_type = data[offset + size]
    if comp_type == 0:
        return raw
    if comp_type in (2, 4):
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    raise ValueError(f"Unsupported LevelDB block compression type: {comp_type}")


def _iter_data_block(block: bytes):
    if len(block) < 4:
        return
    num_restarts = struct.unpack_from("<I", block, len(block) - 4)[0]
    end = len(block) - 4 - num_restarts * 4
    pos = 0
    prev_key = b""
    while pos < end:
        shared, pos = _read_varint(block, pos)
        unshared, pos = _read_varint(block, pos)
        vlen, pos = _read_varint(block, pos)
        key = prev_key[:shared] + block[pos : pos + unshared]
        pos += unshared
        val = block[pos : pos + vlen]
        pos += vlen
        prev_key = key
        if len(key) >= 8:
            yield key[:-8], key[-8:], val


def _read_nbt_payload(buf: bytes, pos: int, tag_id: int) -> tuple[Any, int]:
    if tag_id == 1:  # Byte
        return struct.unpack_from("<b", buf, pos)[0], pos + 1
    if tag_id == 2:  # Short
        return struct.unpack_from("<h", buf, pos)[0], pos + 2
    if tag_id == 3:  # Int
        return struct.unpack_from("<i", buf, pos)[0], pos + 4
    if tag_id == 4:  # Long
        return struct.unpack_from("<q", buf, pos)[0], pos + 8
    if tag_id == 5:  # Float
        return struct.unpack_from("<f", buf, pos)[0], pos + 4
    if tag_id == 6:  # Double
        return struct.unpack_from("<d", buf, pos)[0], pos + 8
    if tag_id == 7:  # ByteArray
        length = struct.unpack_from("<i", buf, pos)[0]
        return buf[pos + 4 : pos + 4 + length], pos + 4 + length
    if tag_id == 8:  # String
        slen = struct.unpack_from("<H", buf, pos)[0]
        return buf[pos + 2 : pos + 2 + slen].decode("utf-8", errors="replace"), pos + 2 + slen
    if tag_id == 9:  # List
        elem_type = buf[pos]
        length = struct.unpack_from("<i", buf, pos + 1)[0]
        pos += 5
        items = []
        for _ in range(max(0, length)):
            item, pos = _read_nbt_payload(buf, pos, elem_type)
            items.append(item)
        return items, pos
    if tag_id == 10:  # Compound
        out: dict[str, Any] = {}
        while pos < len(buf):
            sub_id = buf[pos]
            pos += 1
            if sub_id == 0:
                break
            nlen = struct.unpack_from("<H", buf, pos)[0]
            name = buf[pos + 2 : pos + 2 + nlen].decode("utf-8", errors="replace")
            pos += 2 + nlen
            val, pos = _read_nbt_payload(buf, pos, sub_id)
            out[name] = val
        return out, pos
    if tag_id == 11:  # IntArray
        length = struct.unpack_from("<i", buf, pos)[0]
        end = pos + 4 + length * 4
        return list(struct.unpack_from(f"<{length}i", buf, pos + 4)), end
    if tag_id == 12:  # LongArray
        length = struct.unpack_from("<i", buf, pos)[0]
        end = pos + 4 + length * 8
        return list(struct.unpack_from(f"<{length}q", buf, pos + 4)), end
    raise ValueError(f"Unknown NBT tag id: {tag_id}")


def _read_nbt_root(buf: bytes, pos: int) -> tuple[dict[str, Any], int]:
    tag_id = buf[pos]
    pos += 1
    if tag_id != 10:
        raise ValueError(f"Expected root compound tag (10), got {tag_id}")
    nlen = struct.unpack_from("<H", buf, pos)[0]
    pos += 2 + nlen
    val, pos = _read_nbt_payload(buf, pos, 10)
    return val, pos


def read_level_dat_spawn(world_dir: Path) -> tuple[float, float, float]:
    """Read `(SpawnX, SpawnY, SpawnZ)` from Bedrock `level.dat`, or default `(0.5, 65.0, 0.5)`."""
    level_dat = world_dir / "level.dat"
    if not level_dat.is_file():
        return (0.5, 65.0, 0.5)
    raw = level_dat.read_bytes()
    if len(raw) <= 8:
        return (0.5, 65.0, 0.5)
    data = raw[8:]

    def _find_int(key: bytes, fallback: int) -> int:
        idx = data.find(key)
        if idx < 3:
            return fallback
        after = idx + len(key)
        if data[idx - 3] == 3 and after + 4 <= len(data):
            return struct.unpack_from("<i", data, after)[0]
        return fallback

    sx = _find_int(b"SpawnX", 0)
    sy = max(-64, min(319, _find_int(b"SpawnY", 65)))
    sz = _find_int(b"SpawnZ", 0)
    return (float(sx) + 0.5, float(sy), float(sz) + 0.5)


def resolve_import_world_dir(folder: Path) -> Path | None:
    """Locate the directory containing `db/` inside an `import/<name>` folder."""
    if (folder / "db").is_dir():
        return folder
    for child in folder.iterdir():
        if child.is_dir() and (child / "db").is_dir():
            return child
    return None


def decode_subchunk_blocks(
    raw: bytes,
    fallback_sy: int,
) -> list[tuple[int, int, int, str, dict[str, Any]]]:
    """Decode Bedrock SubChunk v8 or v9 into `(lx, y, lz, block_name, states)` tuples (excluding air)."""
    if len(raw) < 3:
        return []
    version = raw[0]
    if version not in (8, 9):
        return []
    num_layers = raw[1]
    pos = 2
    sy = fallback_sy
    if version == 9:
        sy = struct.unpack_from("<b", raw, pos)[0]
        pos += 1
    if num_layers < 1 or pos >= len(raw):
        return []

    flags = raw[pos]
    pos += 1
    bits_per_block = flags >> 1
    if bits_per_block == 0:
        # Single-block subchunk
        if pos >= len(raw):
            return []
        entry, _ = _read_nbt_root(raw, pos)
        bname = str(entry.get("name", "minecraft:air"))
        if bname == "minecraft:air":
            return []
        states = entry.get("states") or {}
        base_y = sy * 16
        return [
            (lx, base_y + ly, lz, bname, states)
            for lx in range(16)
            for lz in range(16)
            for ly in range(16)
        ]

    blocks_per_word = 32 // bits_per_block
    if blocks_per_word <= 0:
        return []
    word_count = math.ceil(4096 / blocks_per_word)
    words_end = pos + word_count * 4
    if words_end + 4 > len(raw):
        return []

    words = struct.unpack_from(f"<{word_count}I", raw, pos)
    pos = words_end
    palette_len = struct.unpack_from("<i", raw, pos)[0]
    pos += 4
    if palette_len <= 0:
        return []

    palette: list[tuple[str, dict[str, Any]]] = []
    for _ in range(palette_len):
        entry, pos = _read_nbt_root(raw, pos)
        bname = str(entry.get("name", "minecraft:air"))
        states = entry.get("states") or {}
        palette.append((bname, states))

    mask = (1 << bits_per_block) - 1
    base_y = sy * 16
    blocks: list[tuple[int, int, int, str, dict[str, Any]]] = []
    idx = 0
    for word in words:
        w = word
        for _ in range(blocks_per_word):
            if idx >= 4096:
                break
            p_idx = w & mask
            w >>= bits_per_block
            if p_idx < palette_len:
                bname, states = palette[p_idx]
                if bname != "minecraft:air":
                    lx = (idx >> 8) & 0xF
                    lz = (idx >> 4) & 0xF
                    ly = idx & 0xF
                    blocks.append((lx, base_y + ly, lz, bname, states))
            idx += 1
        if idx >= 4096:
            break
    return blocks


class BedrockWorldImporter:
    """Indexes and streams Overworld chunks from an exported Bedrock LevelDB world directory."""

    def __init__(self, world_dir: Path) -> None:
        self.world_dir = world_dir
        self.db_dir = world_dir / "db"
        self._ldb_paths: list[Path] = []
        # (src_cx, src_cz) -> dict[sy, tuple[seq, ldb_idx, block_off, block_sz]]
        self._index: dict[tuple[int, int], dict[int, tuple[int, int, int, int]]] = {}
        self._indexed = False

    def ensure_indexed(self) -> None:
        if self._indexed or not self.db_dir.is_dir():
            return
        self._ldb_paths = sorted(self.db_dir.glob("*.ldb"))
        for ldb_idx, path in enumerate(self._ldb_paths):
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if len(data) < 48 or data[-8:] != _LEVELDB_FOOTER_MAGIC:
                continue
            pos = len(data) - 48
            _, pos = _read_varint(data, pos)
            _, pos = _read_varint(data, pos)
            idx_off, pos = _read_varint(data, pos)
            idx_sz, _ = _read_varint(data, pos)
            idx_block = _read_sstable_block(data, idx_off, idx_sz)
            for _, _, handle in _iter_data_block(idx_block):
                hpos = 0
                boff, hpos = _read_varint(handle, hpos)
                bsz, _ = _read_varint(handle, hpos)
                dblock = _read_sstable_block(data, boff, bsz)
                for ukey, trailer, _val in _iter_data_block(dblock):
                    # Overworld subchunk key: 10 bytes, tag=0x2F, value_type=1
                    if trailer[0] != 1 or len(ukey) != 10 or ukey[8] != _SUBCHUNK_TAG:
                        continue
                    seq = int.from_bytes(trailer, "little") >> 8
                    cx, cz, _, sy = struct.unpack("<iiBb", ukey)
                    chunk_map = self._index.setdefault((cx, cz), {})
                    prev = chunk_map.get(sy)
                    if prev is None or seq > prev[0]:
                        chunk_map[sy] = (seq, ldb_idx, boff, bsz)
        self._indexed = True

    def get_chunk_bounds(self) -> tuple[int, int, int, int] | None:
        """Return `(min_cx, max_cx, min_cz, max_cz)` for all indexed Overworld chunks."""
        self.ensure_indexed()
        if not self._index:
            return None
        cxs = [k[0] for k in self._index]
        czs = [k[1] for k in self._index]
        return (min(cxs), max(cxs), min(czs), max(czs))

    def has_chunk(self, src_cx: int, src_cz: int) -> bool:
        self.ensure_indexed()
        return (src_cx, src_cz) in self._index

    def read_chunk_blocks(
        self,
        src_cx: int,
        src_cz: int,
    ) -> list[tuple[int, int, int, str, dict[str, Any]]]:
        """Load and decode all non-air blocks for source chunk `(src_cx, src_cz)`."""
        self.ensure_indexed()
        sy_map = self._index.get((src_cx, src_cz))
        if not sy_map:
            return []

        # Group subchunks by (ldb_idx, block_off, block_sz) so each data block is decompressed once
        by_block: dict[tuple[int, int, int], dict[int, int]] = {}
        for sy, (_seq, ldb_idx, boff, bsz) in sy_map.items():
            by_block.setdefault((ldb_idx, boff, bsz), {})[sy] = _seq

        results: list[tuple[int, int, int, str, dict[str, Any]]] = []
        target_prefix = struct.pack("<iiB", src_cx, src_cz, _SUBCHUNK_TAG)

        file_cache: dict[int, bytes] = {}
        for (ldb_idx, boff, bsz), needed_sys in by_block.items():
            if ldb_idx not in file_cache:
                file_cache[ldb_idx] = self._ldb_paths[ldb_idx].read_bytes()
            dblock = _read_sstable_block(file_cache[ldb_idx], boff, bsz)
            for ukey, trailer, val in _iter_data_block(dblock):
                if trailer[0] != 1 or len(ukey) != 10 or not ukey.startswith(target_prefix):
                    continue
                sy = struct.unpack_from("<b", ukey, 9)[0]
                seq = int.from_bytes(trailer, "little") >> 8
                if needed_sys.get(sy) == seq:
                    results.extend(decode_subchunk_blocks(val, sy))
        return results
