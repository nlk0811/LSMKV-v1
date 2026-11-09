"""
Sorted String Table (SSTable)
==============================
Immutable, sorted, on-disk key-value file produced by flushing the memtable
or by compaction.

File layout
-----------
  [ data blocks … ]
  [ index block   ]   — sparse index: first key of each block → (offset, size)
  [ bloom filter  ]   — serialised BloomFilter with CRC
  [ footer        ]   — 32-byte fixed record, always at EOF

Entry wire format (inside data blocks, big-endian):
  key_len : 4 bytes
  val_len : 4 bytes
  flags   : 1 byte   (0x01 = tombstone)
  key     : key_len bytes
  value   : val_len bytes  (empty when tombstone)

Footer:
  index_offset : 8 bytes (uint64)
  bloom_offset : 8 bytes (uint64)
  index_size   : 4 bytes (uint32)
  bloom_size   : 4 bytes (uint32)
  magic        : 8 bytes  b'SSTABLE1'
"""

import os
import struct
import zlib
from typing import Iterator, List, Optional, Tuple

from .bloom_filter import BloomFilter
from ._utils import fsync_fd

BLOCK_SIZE    = 4096
_ENTRY_FMT    = '>IIB'
_ENTRY_HDR    = struct.calcsize(_ENTRY_FMT)   # 9 bytes (key_len, val_len, flags)
_FOOTER_FMT   = '>QQII8s'
_FOOTER_SIZE  = struct.calcsize(_FOOTER_FMT)  # 32 bytes
_MAGIC        = b'SSTABLE1'


class SSTableWriter:
    def __init__(self, path: str, bloom_capacity: int = 10_000):
        self.path = path
        self._f = open(path, 'wb')
        self._index = []
        self._bloom = BloomFilter(max(bloom_capacity, 100))
        self._buf = bytearray()
        self._file_off = 0
        self._block_first_key = None

    def add(self, key: bytes, value: bytes, tombstone: bool = False):
        value = b'' if tombstone else value
        entry = struct.pack(_ENTRY_FMT, len(key), len(value), int(tombstone)) + key + value
        # A single oversized entry occupies its own block.
        if self._buf and len(self._buf) + len(entry) > BLOCK_SIZE:
            self._flush_block()
        if self._block_first_key is None:
            self._block_first_key = key
        self._buf.extend(entry)
        self._bloom.add(key)
        if len(self._buf) >= BLOCK_SIZE:
            self._flush_block()

    def _flush_block(self):
        if not self._buf:
            return
        self._index.append((self._block_first_key, self._file_off, len(self._buf)))
        self._f.write(self._buf)
        self._file_off += len(self._buf)
        self._buf = bytearray()
        self._block_first_key = None

    def finish(self) -> int:
        """Finalise the file and return its size in bytes."""
        self._flush_block()

        # ── index block ──
        index_offset = self._file_off
        idx_body = struct.pack('>I', len(self._index))
        for (fk, off, sz) in self._index:
            idx_body += struct.pack('>I', len(fk)) + fk + struct.pack('>QI', off, sz)
        idx_crc  = struct.pack('>I', zlib.crc32(idx_body) & 0xFFFFFFFF)
        idx_data = idx_body + idx_crc
        self._f.write(idx_data)

        # ── bloom filter block ──
        bloom_offset = index_offset + len(idx_data)
        bloom_body   = self._bloom.serialize()
        bloom_crc    = struct.pack('>I', zlib.crc32(bloom_body) & 0xFFFFFFFF)
        bloom_data   = bloom_body + bloom_crc
        self._f.write(bloom_data)

        # ── footer ──
        footer = struct.pack(_FOOTER_FMT,
                             index_offset, bloom_offset,
                             len(idx_data), len(bloom_data),
                             _MAGIC)
        self._f.write(footer)
        self._f.flush()
        fsync_fd(self._f.fileno())
        self._f.close()
        return os.path.getsize(self.path)



class SSTableReader:
    def __init__(self, path: str):
        self.path = path
        self._f = open(path, 'rb')
        try:
            self._load_footer()
            self._load_index()
            self._load_bloom()
        except BaseException:
            self._f.close()
            raise

    def _load_footer(self):
        self._f.seek(-_FOOTER_SIZE, 2)
        raw = self._f.read(_FOOTER_SIZE)
        self._idx_off, self._bloom_off, self._idx_sz, self._bloom_sz, magic = \
            struct.unpack(_FOOTER_FMT, raw)
        if magic != _MAGIC:
            raise ValueError(f'Bad SSTable magic in {self.path}: {magic!r}')

    def _load_index(self):
        self._f.seek(self._idx_off)
        raw      = self._f.read(self._idx_sz)
        body, crc_b = raw[:-4], raw[-4:]
        if (zlib.crc32(body) & 0xFFFFFFFF) != struct.unpack('>I', crc_b)[0]:
            raise ValueError(f'Index CRC mismatch in {self.path}')
        pos   = 0
        count = struct.unpack('>I', body[pos:pos+4])[0]; pos += 4
        self._index: List[Tuple[bytes, int, int]] = []
        for _ in range(count):
            klen          = struct.unpack('>I', body[pos:pos+4])[0]; pos += 4
            key           = body[pos:pos+klen];                       pos += klen
            off, sz       = struct.unpack('>QI', body[pos:pos+12]);   pos += 12
            self._index.append((key, off, sz))

    def _load_bloom(self):
        self._f.seek(self._bloom_off)
        raw      = self._f.read(self._bloom_sz)
        body, crc_b = raw[:-4], raw[-4:]
        if (zlib.crc32(body) & 0xFFFFFFFF) != struct.unpack('>I', crc_b)[0]:
            raise ValueError(f'Bloom CRC mismatch in {self.path}')
        self._bloom = BloomFilter.deserialize(body)


    def may_contain(self, key: bytes) -> bool:
        return self._bloom.may_contain(key)

    def get(self, key: bytes):
        if not self._index or not self.may_contain(key):
            return None
        _, offset, size = self._index[self._block_idx(key)]
        self._f.seek(offset)
        return self._search_block(self._f.read(size), key)

    def scan(self, start=None, end=None):
        first = self._block_idx(start) if start is not None else 0
        for first_key, offset, size in self._index[first:]:
            if end is not None and first_key >= end:
                return
            self._f.seek(offset)
            data = self._f.read(size)
            pos = 0
            while pos < len(data):
                klen, vlen, flags = struct.unpack_from(_ENTRY_FMT, data, pos)
                pos += _ENTRY_HDR
                key = data[pos:pos+klen]
                pos += klen
                value = data[pos:pos+vlen]
                pos += vlen
                if start is not None and key < start:
                    continue
                if end is not None and key >= end:
                    return
                yield key, value, bool(flags & 1)

    def _block_idx(self, key: bytes) -> int:
        """Binary search: last block whose first_key <= key."""
        lo, hi, result = 0, len(self._index) - 1, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if self._index[mid][0] <= key:
                result = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return result

    def _search_block(self, data: bytes, target: bytes) -> Optional[Tuple[bytes, bool]]:
        """Linear scan of an old-format (non-prefix-compressed) block."""
        pos = 0
        while pos < len(data):
            if pos + _ENTRY_HDR > len(data):
                break
            klen, vlen, flags = struct.unpack(_ENTRY_FMT, data[pos:pos+_ENTRY_HDR])
            pos  += _ENTRY_HDR
            key   = data[pos:pos+klen]; pos += klen
            value = data[pos:pos+vlen]; pos += vlen
            if key == target:
                return (value, bool(flags & 0x01))
        return None


    @property
    def first_key(self):
        return self._index[0][0] if self._index else None

    def close(self):
        self._f.close()
