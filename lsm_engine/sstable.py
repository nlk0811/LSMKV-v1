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

import mmap
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
        self._mm = None
        try:
            size = os.fstat(self._f.fileno()).st_size
            # Empty/corrupt tiny files still go through footer validation below.
            if size > 0:
                self._mm = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)
                # mmap owns its descriptor; retaining the original doubles
                # descriptor use and is unnecessary for immutable reads.
                self._f.close()
            self._load_footer(size)
            self._load_index()
            self._load_bloom()
            self._last_key = self._discover_last_key()
        except BaseException:
            self.close()
            raise

    def _slice(self, offset: int, size: int) -> bytes:
        """Return a bytes slice of the mmap (one copy of the requested span)."""
        return self._mm[offset:offset + size]

    def _load_footer(self, size: int):
        if size < _FOOTER_SIZE:
            raise ValueError(f'Bad SSTable magic in {self.path}: file too small')
        raw = self._mm[size - _FOOTER_SIZE:size]
        self._idx_off, self._bloom_off, self._idx_sz, self._bloom_sz, magic = \
            struct.unpack(_FOOTER_FMT, raw)
        if magic != _MAGIC:
            raise ValueError(f'Bad SSTable magic in {self.path}: {magic!r}')

    def _load_index(self):
        raw = self._slice(self._idx_off, self._idx_sz)
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
        raw = self._slice(self._bloom_off, self._bloom_sz)
        body, crc_b = raw[:-4], raw[-4:]
        if (zlib.crc32(body) & 0xFFFFFFFF) != struct.unpack('>I', crc_b)[0]:
            raise ValueError(f'Bloom CRC mismatch in {self.path}')
        self._bloom = BloomFilter.deserialize(body)

    def _discover_last_key(self) -> Optional[bytes]:
        """Walk the final data block once so point reads can reject out-of-range keys."""
        if not self._index:
            return None
        _, offset, size = self._index[-1]
        data = self._slice(offset, size)
        pos = 0
        last = None
        n = len(data)
        while pos + _ENTRY_HDR <= n:
            klen, vlen, _flags = struct.unpack_from(_ENTRY_FMT, data, pos)
            pos += _ENTRY_HDR
            last = data[pos:pos + klen]
            pos += klen + vlen
        return last


    def may_contain(self, key: bytes) -> bool:
        return self._bloom.may_contain(key)

    def get(self, key: bytes):
        if not self._index:
            return None
        # Cheap range gate before the Bloom filter (helps missing-key workloads).
        if key < self._index[0][0] or (self._last_key is not None and key > self._last_key):
            return None
        if not self.may_contain(key):
            return None
        _, offset, size = self._index[self._block_idx(key)]
        return self._search_block(self._slice(offset, size), key)

    def scan(self, start=None, end=None):
        first = self._block_idx(start) if start is not None else 0
        # Avoid slicing the rest of a potentially large sparse index for each
        # short range scan. Most scans visit only a handful of blocks.
        unpack_from = struct.unpack_from
        hdr = _ENTRY_HDR
        fmt = _ENTRY_FMT
        for block in range(first, len(self._index)):
            first_key, offset, size = self._index[block]
            if end is not None and first_key >= end:
                return
            data = self._slice(offset, size)
            pos = 0
            n = len(data)
            while pos + hdr <= n:
                klen, vlen, flags = unpack_from(fmt, data, pos)
                pos += hdr
                key = data[pos:pos + klen]
                pos += klen
                if start is not None and key < start:
                    pos += vlen
                    continue
                if end is not None and key >= end:
                    return
                value = data[pos:pos + vlen]
                pos += vlen
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
        """Linear scan of a sorted block; stop once keys pass the target."""
        pos = 0
        n = len(data)
        unpack_from = struct.unpack_from
        hdr = _ENTRY_HDR
        fmt = _ENTRY_FMT
        while pos + hdr <= n:
            klen, vlen, flags = unpack_from(fmt, data, pos)
            pos += hdr
            end_key = pos + klen
            key = data[pos:end_key]
            if key == target:
                end_val = end_key + vlen
                return (data[end_key:end_val], bool(flags & 0x01))
            if key > target:
                return None
            pos = end_key + vlen
        return None


    @property
    def first_key(self):
        return self._index[0][0] if self._index else None

    @property
    def last_key(self):
        return self._last_key

    def close(self):
        if self._mm is not None:
            self._mm.close()
            self._mm = None
        if not self._f.closed:
            self._f.close()
