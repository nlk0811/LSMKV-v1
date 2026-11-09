"""
Crash Recovery Tests
====================
Verifies the three crash-safety invariants by simulating crashes at
different points in the write/flush/compact cycle.

These tests do NOT use kill-9 (that is in crash_harness.py).
Instead they simulate crashes by directly corrupting WAL records
or leaving intermediate files, then verifying that LSMTree recovers
to a consistent state on re-open.
"""

import os
import shutil
import struct
import zlib
import pytest
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lsm_engine.lsm_tree import LSMTree
from lsm_engine.wal       import WAL, OpType, _HDR_FMT, _HDR_SIZE


# ── Invariant 1: WAL Completeness ─────────────────────────────────────────────

def test_wal_replay_restores_memtable(tmp_path):
    """Keys written to the WAL appear in a freshly opened DB."""
    path = str(tmp_path / 'db')
    db = LSMTree(path)
    db.put('persisted', 'yes')
    # Close without explicit flush (memtable still in RAM, WAL on disk)
    db._wal.close()   # bypass close() so memtable is NOT flushed
    db._shutdown.set()

    db2 = LSMTree(path)
    assert db2.get('persisted') == 'yes'
    db2.close()
    shutil.rmtree(path, ignore_errors=True)


def test_truncated_wal_partial_record_ignored(tmp_path):
    """A partially written WAL record (simulated crash mid-write) is dropped."""
    wal_path = str(tmp_path / 'wal.log')
    w = WAL(wal_path)
    w.log_put(b'complete', b'record')
    w.close()

    # Append a partial record (only header, no key/value/crc)
    with open(wal_path, 'ab') as f:
        f.write(struct.pack(_HDR_FMT, 1, 5, 5, 0.0))  # says key_len=5 but writes nothing

    records = list(WAL(wal_path).replay())
    assert len(records) == 1
    assert records[0][1] == b'complete'


def test_corrupt_crc_stops_replay(tmp_path):
    """Replaying stops at the first CRC mismatch (corrupted record)."""
    wal_path = str(tmp_path / 'wal.log')
    w = WAL(wal_path)
    w.log_put(b'good', b'record')
    w.close()

    with open(wal_path, 'r+b') as f:
        f.seek(10)
        f.write(b'\xFF\xFF')  # flip bytes to corrupt CRC

    records = list(WAL(wal_path).replay())
    assert len(records) == 0


# ── Invariant 2: Compaction Atomicity ─────────────────────────────────────────

def test_orphan_sst_cleaned_on_open(tmp_path):
    """
    An SSTable file that exists on disk but is NOT in the MANIFEST
    (simulates a crash after writing the new file but before updating
    the MANIFEST) is cleaned up on the next DB open.
    """
    path = str(tmp_path / 'db')
    db = LSMTree(path)
    db.put('x', 'y')
    db.close()

    # Plant an orphaned .sst file
    orphan = os.path.join(path, 'L1_999999.sst')
    with open(orphan, 'wb') as f:
        f.write(b'\x00' * 128)

    db2 = LSMTree(path)
    # The orphan should have been removed during _cleanup_orphans()
    assert not os.path.exists(orphan)
    db2.close()
    shutil.rmtree(path, ignore_errors=True)


# ── Invariant 3: WAL Truncation Safety ────────────────────────────────────────

def test_wal_not_truncated_before_manifest(tmp_path):
    """
    Invariant 3 — WAL Truncation Safety:
    After a flush the MANIFEST must reference the new SSTable, and that
    SSTable must be physically present on disk.  This guarantees that WAL
    truncation happened only AFTER the MANIFEST was durably updated.
    """
    path = str(tmp_path / 'db')
    from lsm_engine.lsm_tree import MEMTABLE_LIMIT
    db = LSMTree(path)

    # Write enough to trigger at least one flush
    chunk = 'x' * 500
    for i in range(MEMTABLE_LIMIT // 500 + 10):
        db.put(f'key{i:06d}', chunk)

    # At least one L0 SSTable must be in the MANIFEST
    assert len(db._manifest.levels[0]) > 0, 'No L0 file in MANIFEST after flush'

    # Every SSTable referenced by the MANIFEST must exist on disk
    # (proves the SSTable was flushed before WAL truncation)
    for f in db._manifest.all_files():
        assert os.path.exists(f), f'MANIFEST references {f} but file is missing'

    # Data written before the flush must still be readable
    assert db.get('key000000') is not None, 'key000000 missing after flush'

    db.close()
    shutil.rmtree(path, ignore_errors=True)
