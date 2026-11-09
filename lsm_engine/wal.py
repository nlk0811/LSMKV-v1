"""CRC32 write-ahead log. Sync mode fsyncs every record before returning."""
import os
import struct
import time
import zlib
from enum import IntEnum
from typing import Iterator, Tuple
from ._utils import fsync_fd

_HDR_FMT = '>BIId'
_HDR_SIZE = struct.calcsize(_HDR_FMT)
_CRC_SIZE = 4

class OpType(IntEnum):
    PUT = 1
    DELETE = 2

class WAL:
    def __init__(self, path: str, sync_writes: bool = True):
        self.path = path
        self._sync = sync_writes
        self._f = open(path, 'ab')

    def _append(self, op, key, value):
        header = struct.pack(_HDR_FMT, int(op), len(key), len(value), time.time())
        payload = header + key + value
        self._f.write(payload + struct.pack('>I', zlib.crc32(payload) & 0xffffffff))
        self._f.flush()
        if self._sync:
            fsync_fd(self._f.fileno())

    def log_put(self, key: bytes, value: bytes):
        self._append(OpType.PUT, key, value)

    def log_delete(self, key: bytes):
        self._append(OpType.DELETE, key, b'')

    def replay(self) -> Iterator[Tuple[OpType, bytes, bytes]]:
        """Replay the valid prefix, then discard an incomplete/corrupt tail.

        Repairing the tail before accepting new writes prevents a later replay
        from stopping at old crash debris and hiding subsequently logged keys.
        Recovery must exhaust this iterator before new writes begin.
        """
        valid_end = 0
        with open(self.path, 'rb') as source:
            remaining = os.fstat(source.fileno()).st_size
            while remaining >= _HDR_SIZE:
                header = source.read(_HDR_SIZE)
                op, klen, vlen, timestamp = struct.unpack(_HDR_FMT, header)
                size = _HDR_SIZE + klen + vlen + _CRC_SIZE
                if op not in (OpType.PUT, OpType.DELETE) or size > remaining:
                    break
                payload = source.read(klen + vlen)
                expected = struct.unpack('>I', source.read(_CRC_SIZE))[0]
                if expected != zlib.crc32(header + payload) & 0xffffffff:
                    break
                valid_end += size
                remaining -= size
                yield OpType(op), payload[:klen], payload[klen:]
        if os.path.getsize(self.path) != valid_end:
            self._f.truncate(valid_end)
            self._f.flush()
            fsync_fd(self._f.fileno())

    def truncate(self):
        """Caller must first durably register all covered records in MANIFEST."""
        self._f.seek(0)
        self._f.truncate()
        self._f.flush()
        fsync_fd(self._f.fileno())

    def close(self):
        self._f.close()

    @property
    def size_bytes(self):
        return os.path.getsize(self.path)
