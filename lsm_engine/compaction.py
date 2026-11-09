"""
K-Way Merge Compaction
=======================
Merges N sorted SSTables into one output SSTable using a min-heap.

Algorithm
---------
  1. Create one scan-iterator per input SSTable, ordered newest → oldest.
  2. Prime a min-heap with the first entry from each iterator.
     Heap key: (entry_key, reader_sequence).
     reader_sequence 0 = newest, so for equal keys the newest version is
     popped first.
  3. Pop the minimum.  Advance that reader and push its next entry.
  4. If the current key equals the previous yielded key → skip (dedup).
  5. If the entry is a tombstone AND we are at the deepest compaction level
     (no older data below) → drop it entirely.  Otherwise emit it.

Complexity
----------
  Time:  O(N log K)   N = total entries, K = number of input files
  Space: O(K)         heap holds at most one entry per file
"""

import heapq
import os
from dataclasses import dataclass, field
from typing import Any, Iterator, List, Optional, Tuple

from .sstable import SSTableReader, SSTableWriter


@dataclass(order=True)
class _E:
    key:   bytes
    seq:   int               # lower = newer/higher priority
    val:   bytes = field(compare=False, default=b'')
    tomb:  bool  = field(compare=False, default=False)
    it:    Any   = field(compare=False, default=None)


def kway_merge(
    readers: List[SSTableReader],
    start:   Optional[bytes] = None,
    end:     Optional[bytes] = None,
    drop_tombstones: bool    = False,
) -> Iterator[Tuple[bytes, bytes, bool]]:
    """
    Merge readers (newest first) into a sorted, deduplicated stream.
    Yields (key, value, is_tombstone).
    If drop_tombstones is True, tombstone entries are suppressed entirely
    (used during full compaction to the deepest level).
    """
    heap: list = []
    iters = [r.scan(start, end) for r in readers]

    for seq, it in enumerate(iters):
        try:
            k, v, d = next(it)
            heapq.heappush(heap, _E(k, seq, v, d, it))
        except StopIteration:
            pass

    last_key: Optional[bytes] = None
    while heap:
        e = heapq.heappop(heap)
        try:
            k, v, d = next(e.it)
            heapq.heappush(heap, _E(k, e.seq, v, d, e.it))
        except StopIteration:
            pass

        if e.key == last_key:
            continue        # older version of an already-seen key
        last_key = e.key

        if drop_tombstones and e.tomb:
            continue        # safe to drop tombstones at deepest level

        yield e.key, e.val, e.tomb


def compact(input_paths: List[str], output_path: str,
            drop_tombstones: bool = False) -> int:
    """Merge SSTables in newest-first order; return the output entry count."""
    readers = []
    writer = None
    try:
        for path in input_paths:
            readers.append(SSTableReader(path))
        if not readers:
            return 0
        capacity = max(sum(len(r._index) * 100 for r in readers), 100)
        writer = SSTableWriter(output_path, bloom_capacity=capacity)
        written = 0
        for key, value, tombstone in kway_merge(readers, drop_tombstones=drop_tombstones):
            writer.add(key, value, tombstone=tombstone)
            written += 1
        if written:
            writer.finish()
        else:
            writer._f.close()
            os.remove(output_path)
        return written
    finally:
        if writer is not None and not writer._f.closed:
            writer._f.close()
        for reader in readers:
            reader.close()
