"""Low level reader for git object databases.

Supports both loose objects (``.git/objects/xx/yyyy...``) and packfiles
(``.git/objects/pack/*.pack`` + ``*.idx`` version 2), including both delta
encodings used by git: ``OBJ_OFS_DELTA`` (offset deltas) and ``OBJ_REF_DELTA``
(reference deltas).

Only the standard library is used (``zlib``, ``struct``, ``mmap``).
"""

from __future__ import annotations

import mmap
import os
import struct
import zlib
from collections import OrderedDict
from typing import Optional, Tuple

OBJ_COMMIT = 1
OBJ_TREE = 2
OBJ_BLOB = 3
OBJ_TAG = 4
OBJ_OFS_DELTA = 6
OBJ_REF_DELTA = 7

TYPE_NAMES = {
    OBJ_COMMIT: "commit",
    OBJ_TREE: "tree",
    OBJ_BLOB: "blob",
    OBJ_TAG: "tag",
}

_IDX_MAGIC = 0xFF744F63  # "\377tOc"


class GitObjectError(Exception):
    """Raised when the object database cannot be parsed."""


def _read_varint(data: bytes, pos: int) -> Tuple[int, int]:
    """Read a little-endian, 7-bits-per-byte variable length integer."""
    result = 0
    shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            break
        shift += 7
    return result, pos


def apply_delta(base: bytes, delta: bytes) -> bytes:
    """Apply a git delta to ``base`` and return the reconstructed object."""
    pos = 0
    base_size, pos = _read_varint(delta, pos)
    result_size, pos = _read_varint(delta, pos)
    if base_size != len(base):
        raise GitObjectError(
            "delta base size mismatch: header=%d actual=%d" % (base_size, len(base))
        )

    out = bytearray()
    end = len(delta)
    while pos < end:
        opcode = delta[pos]
        pos += 1
        if opcode & 0x80:
            copy_offset = 0
            copy_size = 0
            for i in range(4):
                if opcode & (1 << i):
                    copy_offset |= delta[pos] << (i * 8)
                    pos += 1
            for i in range(3):
                if opcode & (1 << (4 + i)):
                    copy_size |= delta[pos] << (i * 8)
                    pos += 1
            if copy_size == 0:
                copy_size = 0x10000
            out += base[copy_offset : copy_offset + copy_size]
        elif opcode:
            out += delta[pos : pos + opcode]
            pos += opcode
        else:
            raise GitObjectError("invalid delta opcode 0")

    if len(out) != result_size:
        raise GitObjectError(
            "delta result size mismatch: header=%d actual=%d" % (result_size, len(out))
        )
    return bytes(out)


class PackFile:
    """A single ``.pack`` file together with its ``.idx`` lookup table."""

    def __init__(self, pack_path: str):
        self.pack_path = pack_path
        self.idx_path = pack_path[:-5] + ".idx"
        self._file = open(pack_path, "rb")
        try:
            self._pack = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError:
            self._pack = b""
        magic = self._pack[:4]
        if magic != b"PACK":
            raise GitObjectError("not a pack file: %s" % pack_path)
        self.version, self.num_objects = struct.unpack(">II", self._pack[4:12])
        self._parse_idx()

    def _parse_idx(self) -> None:
        with open(self.idx_path, "rb") as fh:
            data = fh.read()
        magic, version = struct.unpack(">II", data[:8])
        if magic != _IDX_MAGIC:
            raise GitObjectError("unsupported idx (v1) for %s" % self.idx_path)
        if version != 2:
            raise GitObjectError("unsupported idx version %d" % version)

        self.fanout = struct.unpack(">256I", data[8 : 8 + 1024])
        n = self.fanout[255]
        self.num_objects = n
        pos = 8 + 1024

        self.shas = data[pos : pos + 20 * n]
        pos += 20 * n
        self.crcs = data[pos : pos + 4 * n]
        pos += 4 * n
        raw_offsets = struct.unpack(">%dI" % n, data[pos : pos + 4 * n]) if n else ()
        pos += 4 * n

        num_large = sum(1 for off in raw_offsets if off & 0x80000000)
        large = (
            struct.unpack(">%dQ" % num_large, data[pos : pos + 8 * num_large])
            if num_large
            else ()
        )
        self.offsets = [
            large[off & 0x7FFFFFFF] if off & 0x80000000 else off for off in raw_offsets
        ]

    def _sha_at(self, index: int) -> bytes:
        return self.shas[index * 20 : (index + 1) * 20]

    def find(self, sha: bytes) -> Optional[int]:
        """Return the pack offset for a 20-byte sha, or ``None``."""
        first = self.fanout[sha[0] - 1] if sha[0] > 0 else 0
        last = self.fanout[sha[0]]
        while first < last:
            mid = (first + last) // 2
            candidate = self._sha_at(mid)
            if candidate == sha:
                return self.offsets[mid]
            if candidate < sha:
                first = mid + 1
            else:
                last = mid
        return None

    def read_raw(self, offset: int):
        """Read the raw entry at ``offset``.

        Returns ``(obj_type, size, data, base_offset, base_sha)`` where the data
        is still zlib-inflated but not yet delta-resolved.
        """
        pack = self._pack
        pos = offset
        byte = pack[pos]
        pos += 1
        obj_type = (byte >> 4) & 0x07
        size = byte & 0x0F
        shift = 4
        while byte & 0x80:
            byte = pack[pos]
            pos += 1
            size |= (byte & 0x7F) << shift
            shift += 7

        base_offset = None
        base_sha = None
        if obj_type == OBJ_OFS_DELTA:
            byte = pack[pos]
            pos += 1
            rel = byte & 0x7F
            while byte & 0x80:
                byte = pack[pos]
                pos += 1
                rel = ((rel + 1) << 7) | (byte & 0x7F)
            base_offset = offset - rel
        elif obj_type == OBJ_REF_DELTA:
            base_sha = pack[pos : pos + 20]
            pos += 20

        data = self._inflate(pos, size)
        return obj_type, size, data, base_offset, base_sha

    def _inflate(self, pos: int, expected_size: int) -> bytes:
        decompressor = zlib.decompressobj()
        out = bytearray()
        total = len(self._pack)
        chunk = 65536
        while pos < total:
            end = min(pos + chunk, total)
            out += decompressor.decompress(self._pack[pos:end])
            pos = end
            if decompressor.eof:
                break
        if not decompressor.eof:
            raise GitObjectError("truncated zlib stream in %s" % self.pack_path)
        return bytes(out)

    def close(self) -> None:
        try:
            self._pack.close()
        except (AttributeError, ValueError):
            pass
        self._file.close()


class ObjectReader:
    """Reads git objects by sha from loose storage and packfiles."""

    def __init__(self, git_dir: str, cache_size: int = 512):
        self.git_dir = git_dir
        self.objects_dir = os.path.join(git_dir, "objects")
        self._cache: "OrderedDict[bytes, Tuple[str, bytes]]" = OrderedDict()
        self._cache_size = cache_size
        self._offset_cache: "OrderedDict[Tuple[int, int], Tuple[str, bytes]]" = (
            OrderedDict()
        )
        self.packs = []
        pack_dir = os.path.join(self.objects_dir, "pack")
        if os.path.isdir(pack_dir):
            for name in sorted(os.listdir(pack_dir)):
                if name.endswith(".pack"):
                    try:
                        self.packs.append(PackFile(os.path.join(pack_dir, name)))
                    except GitObjectError:
                        continue

    def _cache_put(self, key, value):
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def read(self, sha_hex: str) -> Tuple[str, bytes]:
        """Return ``(type, content)`` for a hex sha."""
        return self._read_sha(bytes.fromhex(sha_hex))

    def _read_sha(self, sha: bytes) -> Tuple[str, bytes]:
        cached = self._cache.get(sha)
        if cached is not None:
            self._cache.move_to_end(sha)
            return cached

        hexsha = sha.hex()
        loose_path = os.path.join(self.objects_dir, hexsha[:2], hexsha[2:])
        if os.path.exists(loose_path):
            result = self._read_loose(loose_path)
            self._cache_put(sha, result)
            return result

        for pack in self.packs:
            offset = pack.find(sha)
            if offset is not None:
                result = self._read_pack_object(pack, offset)
                self._cache_put(sha, result)
                return result

        raise GitObjectError("object not found: %s" % hexsha)

    @staticmethod
    def _read_loose(path: str) -> Tuple[str, bytes]:
        with open(path, "rb") as fh:
            raw = zlib.decompress(fh.read())
        nul = raw.index(b"\0")
        header = raw[:nul].decode("ascii")
        obj_type, _size = header.split(" ", 1)
        return obj_type, raw[nul + 1 :]

    def _read_pack_object(
        self, pack: PackFile, offset: int, depth: int = 0
    ) -> Tuple[str, bytes]:
        if depth > 64:
            raise GitObjectError("delta chain too deep at offset %d" % offset)

        key = (id(pack), offset)
        cached = self._offset_cache.get(key)
        if cached is not None:
            self._offset_cache.move_to_end(key)
            return cached

        obj_type, _size, data, base_offset, base_sha = pack.read_raw(offset)
        if obj_type in TYPE_NAMES:
            result = (TYPE_NAMES[obj_type], data)
        elif obj_type == OBJ_OFS_DELTA:
            base_type, base_data = self._read_pack_object(pack, base_offset, depth + 1)
            result = (base_type, apply_delta(base_data, data))
        elif obj_type == OBJ_REF_DELTA:
            base_type, base_data = self._read_sha(base_sha)
            result = (base_type, apply_delta(base_data, data))
        else:
            raise GitObjectError("unknown object type %d" % obj_type)

        self._offset_cache[key] = result
        self._offset_cache.move_to_end(key)
        while len(self._offset_cache) > self._cache_size:
            self._offset_cache.popitem(last=False)
        return result

    def close(self) -> None:
        for pack in self.packs:
            pack.close()

    def __enter__(self) -> "ObjectReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
