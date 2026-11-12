"""Version 1 LSM engine described in LSMKV_v1_Research_Dossier.md.

WAL Completeness: log and fsync before updating the memtable.
Compaction Atomicity: the atomic MANIFEST replacement is the commit point.
WAL Truncation Safety: register the durable SSTable before truncating the WAL.
"""
import heapq
import os
import threading
import uuid
from contextlib import ExitStack

from ._utils import fsync_dir
from .compaction import compact
from .manifest import Manifest, MAX_LEVELS
from .skip_list import SkipList
from .sstable import SSTableReader, SSTableWriter
from .wal import WAL, OpType

MEMTABLE_LIMIT = 4 * 1024 * 1024
L0_COMPACT_TRIGGER = 4
BASE_LEVEL_BYTES = 10 * 1024 * 1024
LEVEL_MULTIPLIER = 10


def _b(value):
    return value.encode() if isinstance(value, str) else value


def _s(value):
    return value.decode() if isinstance(value, bytes) else value


class LSMTree:
    def __init__(self, directory: str, sync_writes: bool = True):
        self.dir = os.path.abspath(directory)
        os.makedirs(self.dir, exist_ok=True)
        self._write_lock = threading.RLock()
        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._closed = False
        self._bg_error = None
        self._manifest = Manifest(self.dir)
        self._memtable = SkipList()
        # Open SSTable readers, keyed by path. Each holds its parsed sparse
        # index and Bloom filter, so point reads skip the per-call reload.
        # Guarded by self._lock; entries are dropped before a file is deleted.
        self._readers = {}
        self._reader_pins = {}
        self._retired_readers = {}
        self._cleanup_orphans()
        self._wal = WAL(os.path.join(self.dir, 'wal.log'), sync_writes=sync_writes)
        fsync_dir(self.dir)
        self._recover()
        self._bg = threading.Thread(target=self._bg_loop, daemon=True,
                                    name='lsm-compaction')
        self._bg.start()

    def _check_open(self):
        if self._closed:
            raise ValueError('Database is closed')
        if self._bg_error is not None:
            raise RuntimeError('Background compaction failed') from self._bg_error

    def _reader(self, path):
        """Return the cached reader for path. Caller must hold self._lock."""
        reader = self._readers.get(path)
        if reader is None:
            reader = self._readers[path] = SSTableReader(path)
        return reader

    def _drop_reader(self, path):
        """Forget a cached reader, closing it after any active scans finish."""
        reader = self._readers.pop(path, None)
        if reader is not None:
            if self._reader_pins.get(path, 0):
                self._retired_readers[path] = reader
            else:
                reader.close()

    def _pin_reader(self, path):
        """Get a cached reader and pin its file handle for a scan."""
        reader = self._reader(path)
        self._reader_pins[path] = self._reader_pins.get(path, 0) + 1
        return reader

    def _unpin_reader(self, path):
        """Release a scan's pin; caller must hold self._lock."""
        remaining = self._reader_pins[path] - 1
        if remaining:
            self._reader_pins[path] = remaining
            return
        del self._reader_pins[path]
        retired = self._retired_readers.pop(path, None)
        if retired is not None:
            retired.close()

    def put(self, key, value):
        key, value = _b(key), _b(value)
        with self._write_lock:
            self._check_open()
            self._wal.log_put(key, value)  # Invariant 1: durable before RAM
            with self._lock:
                self._memtable.put(key, value)
            if self._memtable.size_bytes >= MEMTABLE_LIMIT:
                self._flush_memtable()

    def delete(self, key):
        key = _b(key)
        with self._write_lock:
            self._check_open()
            self._wal.log_delete(key)
            with self._lock:
                self._memtable.delete(key)
            if self._memtable.size_bytes >= MEMTABLE_LIMIT:
                self._flush_memtable()

    def get(self, key):
        # Preserve the caller's key type so binary workloads skip UTF-8 decode.
        as_str = isinstance(key, str)
        key = _b(key)
        # The lock keeps compaction from deleting a file mid-read. Cached
        # readers mmap the SST, so lookups do not share a mutable file offset.
        with self._lock:
            self._check_open()
            hit = self._memtable.get(key)
            if hit is not None:
                value, deleted = hit
                if deleted:
                    return None
                return _s(value) if as_str else value
            for level in self._manifest.levels:
                for path in reversed(level):
                    hit = self._reader(path).get(key)
                    if hit is not None:
                        value, deleted = hit
                        if deleted:
                            return None
                        return _s(value) if as_str else value
        return None

    def scan(self, start=None, end=None):
        """Yield sorted live (key, value) pairs in the half-open range."""
        as_str = isinstance(start, str) if start is not None else True
        start = _b(start) if start is not None else None
        end = _b(end) if end is not None else None
        with ExitStack() as resources:
            with self._lock:
                self._check_open()
                # Snapshot RAM; pin files before compaction can unlink them.
                sources = []
                mem_hits = list(self._memtable.scan(start, end))
                if mem_hits:
                    sources.append(iter(mem_hits))
                for level in self._manifest.levels:
                    for path in reversed(level):
                        reader = self._pin_reader(path)
                        # Skip SSTables whose key range cannot intersect [start, end).
                        first = reader.first_key
                        last = reader.last_key
                        if first is None:
                            resources.callback(self._unpin_scan_reader, path)
                            continue
                        if end is not None and first >= end:
                            resources.callback(self._unpin_scan_reader, path)
                            continue
                        if start is not None and last is not None and last < start:
                            resources.callback(self._unpin_scan_reader, path)
                            continue
                        resources.callback(self._unpin_scan_reader, path)
                        sources.append(reader.scan(start, end))
            # Fast paths: skip the heap when only one or two sources are live.
            if len(sources) == 1:
                yield from self._emit_scan(sources[0], as_str)
                return
            if len(sources) == 2:
                yield from self._merge_two_scans(sources[0], sources[1], as_str)
                return
            heap = []
            for priority, source in enumerate(sources):
                entry = next(source, None)
                if entry is not None:
                    key, value, deleted = entry
                    heapq.heappush(heap, (key, priority, value, deleted, source))
            last_key = None
            while heap:
                key, priority, value, deleted, source = heapq.heappop(heap)
                entry = next(source, None)
                if entry is not None:
                    nk, nv, nd = entry
                    heapq.heappush(heap, (nk, priority, nv, nd, source))
                if key == last_key:
                    continue
                last_key = key
                if not deleted:
                    if as_str:
                        yield _s(key), _s(value)
                    else:
                        yield key, value

    @staticmethod
    def _emit_scan(source, as_str):
        for key, value, deleted in source:
            if not deleted:
                yield (_s(key), _s(value)) if as_str else (key, value)

    @staticmethod
    def _merge_two_scans(newer, older, as_str):
        """Newest-wins merge of two sorted iterators without heapq overhead."""
        a = next(newer, None)
        b = next(older, None)
        last_key = None
        while a is not None and b is not None:
            if a[0] <= b[0]:
                key, value, deleted = a
                a = next(newer, None)
            else:
                key, value, deleted = b
                b = next(older, None)
            if key == last_key:
                continue
            last_key = key
            if not deleted:
                yield (_s(key), _s(value)) if as_str else (key, value)
        rest = a if a is not None else b
        src = newer if a is not None else older
        while rest is not None:
            key, value, deleted = rest
            rest = next(src, None)
            if key == last_key:
                continue
            last_key = key
            if not deleted:
                yield (_s(key), _s(value)) if as_str else (key, value)

    def _unpin_scan_reader(self, path):
        with self._lock:
            self._unpin_reader(path)

    def _flush_memtable(self):
        """Caller holds _write_lock; keep RAM visible until MANIFEST commits."""
        if not len(self._memtable):
            return
        path = os.path.join(self.dir, f'L0_{uuid.uuid4().hex}.sst')
        tmp = path + '.tmp'
        writer = SSTableWriter(tmp, bloom_capacity=max(len(self._memtable), 100))
        try:
            for key, value, deleted in self._memtable:
                writer.add(key, value or b'', tombstone=deleted)
            writer.finish()
        finally:
            if not writer._f.closed:
                writer._f.close()
        os.rename(tmp, path)
        fsync_dir(self.dir)
        with self._lock:
            self._manifest.add_l0(path)
            self._wal.truncate()  # Invariant 3: SSTable + MANIFEST first
            self._memtable = SkipList()

    def _bg_loop(self):
        while not self._shutdown.wait(2):
            try:
                self._maybe_compact()
            except Exception as error:
                self._bg_error = error
                return

    def _maybe_compact(self):
        with self._lock:
            l0_ready = len(self._manifest.levels[0]) >= L0_COMPACT_TRIGGER
        if l0_ready:
            self._compact_into(0)
        for level in range(1, MAX_LEVELS - 1):
            budget = BASE_LEVEL_BYTES * LEVEL_MULTIPLIER ** (level - 1)
            with self._lock:
                over_budget = self._manifest.level_size(level) > budget
            if over_budget:
                self._compact_into(level)

    def _compact_into(self, src_level):
        dst_level = src_level + 1
        # Only the one background worker compacts; writes can add L0 files
        # while the output is built. Remove precisely the snapshotted inputs.
        with self._lock:
            src = list(self._manifest.levels[src_level])
            dst = list(self._manifest.levels[dst_level])
        if not src:
            return
        inputs = list(reversed(src)) + list(reversed(dst))
        path = os.path.join(self.dir, f'L{dst_level}_{uuid.uuid4().hex}.sst')
        tmp = path + '.tmp'
        written = compact(inputs, tmp, drop_tombstones=dst_level == MAX_LEVELS - 1)
        outputs = []
        if written:
            os.rename(tmp, path)
            fsync_dir(self.dir)
            outputs = [path]
        with self._lock:
            # Invariant 2: publish output before deleting any input.
            self._manifest.apply_compaction(
                {src_level: src, dst_level: dst}, {dst_level: outputs})
            for old in inputs:
                self._drop_reader(old)  # close the handle before unlinking
                os.remove(old)

    def _recover(self):
        for op, key, value in self._wal.replay():
            if op == OpType.PUT:
                self._memtable.put(key, value)
            else:
                self._memtable.delete(key)

    def _cleanup_orphans(self):
        known = set(self._manifest.all_files())
        for name in os.listdir(self.dir):
            path = os.path.join(self.dir, name)
            if (name.endswith('.sst') or name.endswith('.sst.tmp')
                    or name == 'MANIFEST.json.tmp') and path not in known:
                os.remove(path)

    def stats(self):
        with self._lock:
            self._check_open()
            return {
                'memtable_bytes': self._memtable.size_bytes,
                'memtable_entries': len(self._memtable),
                'wal_bytes': self._wal.size_bytes,
                'levels': {i: {'files': len(files),
                               'bytes': self._manifest.level_size(i)}
                           for i, files in enumerate(self._manifest.levels) if files},
            }

    def close(self):
        self._shutdown.set()
        self._bg.join()
        with self._write_lock:
            if self._closed:
                return
            self._flush_memtable()
            with self._lock:
                for path, reader in self._readers.items():
                    if self._reader_pins.get(path, 0):
                        self._retired_readers[path] = reader
                    else:
                        reader.close()
                self._readers.clear()
            self._wal.close()
            self._closed = True

    def __enter__(self):
        self._check_open()
        return self

    def __exit__(self, *exc):
        self.close()
