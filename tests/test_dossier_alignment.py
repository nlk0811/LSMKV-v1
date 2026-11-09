"""Regression checks for dossier requirements and publication ordering."""
import json
import struct
from unittest.mock import patch

import pytest
import lsm_engine.lsm_tree as engine
from lsm_engine import LSMTree
from lsm_engine.manifest import Manifest
from lsm_engine.sstable import SSTableReader, SSTableWriter
from lsm_engine.wal import WAL


def test_sync_mode_fsyncs_each_record(tmp_path):
    with patch('lsm_engine.wal.fsync_fd') as sync:
        wal = WAL(str(tmp_path / 'wal'))
        try:
            wal.log_put(b'a', b'1')
            wal.log_delete(b'a')
            assert sync.call_count == 2
        finally:
            wal.close()


def test_raw_sstable_entry_matches_dossier(tmp_path):
    path = str(tmp_path / 'table.sst')
    writer = SSTableWriter(path)
    writer.add(b'abc', b'xyz')
    writer.finish()
    with open(path, 'rb') as source:
        assert source.read(15) == struct.pack('>IIB', 3, 3, 0) + b'abcxyz'
        source.seek(-32, 2)
        assert struct.unpack('>QQII8s', source.read(32))[-1] == b'SSTABLE1'


def test_newest_value_survives_compaction_and_reopen(tmp_path):
    path = str(tmp_path / 'db')
    with LSMTree(path, sync_writes=False) as db:
        db.put('key', 'old')
        db._flush_memtable()
        db.put('key', 'new')
        db._flush_memtable()
        db._compact_into(0)
        assert db.get('key') == 'new'
        assert list(db.scan()) == [('key', 'new')]
        db.delete('key')
        db._flush_memtable()
        db._compact_into(0)
        assert db.get('key') is None
    with LSMTree(path) as db:
        assert list(db.scan()) == []


def test_l1_budget_is_ten_mb(tmp_path):
    with LSMTree(str(tmp_path / 'db'), sync_writes=False) as db:
        with patch.object(db._manifest, 'level_size', side_effect=lambda i: 11*1024*1024 if i == 1 else 0):
            with patch.object(db, '_compact_into') as compact:
                db._maybe_compact()
                compact.assert_called_once_with(1)


def test_manifest_failure_preserves_wal_and_memtable(tmp_path):
    with LSMTree(str(tmp_path / 'db'), sync_writes=False) as db:
        db.put('key', 'value')
        before = db._wal.size_bytes
        with patch.object(db._manifest, 'add_l0', side_effect=OSError('injected')):
            with pytest.raises(OSError, match='injected'):
                db._flush_memtable()
        assert db._wal.size_bytes == before
        assert db.get('key') == 'value'


def test_flush_registers_sstable_before_truncating_wal(tmp_path):
    with LSMTree(str(tmp_path / 'db'), sync_writes=False) as db:
        db.put('key', 'value')
        truncate = db._wal.truncate
        def checked_truncate():
            with open(db._manifest.path) as source:
                files = json.load(source)['levels'][0]
            assert files
            reader = SSTableReader(files[0])
            try:
                assert reader.get(b'key') == (b'value', False)
            finally:
                reader.close()
            truncate()
        with patch.object(db._wal, 'truncate', side_effect=checked_truncate):
            db._flush_memtable()


def test_compaction_failure_before_commit_keeps_inputs(tmp_path):
    with LSMTree(str(tmp_path / 'db'), sync_writes=False) as db:
        db.put('key', 'value')
        db._flush_memtable()
        original = db._manifest.all_files()
        with patch.object(db._manifest, 'apply_compaction', side_effect=OSError('injected')):
            with pytest.raises(OSError, match='injected'):
                db._compact_into(0)
        assert db._manifest.all_files() == original
        assert db.get('key') == 'value'
    with LSMTree(str(tmp_path / 'db')) as db:
        assert db.get('key') == 'value'
        assert not list(tmp_path.glob('db/L1_*.sst'))


def test_missing_manifest_file_is_not_silently_ignored(tmp_path):
    with LSMTree(str(tmp_path / 'db')) as db:
        db.put('key', 'value')
    next((tmp_path / 'db').glob('*.sst')).unlink()
    with pytest.raises(FileNotFoundError, match='missing SSTable'):
        LSMTree(str(tmp_path / 'db'))


@pytest.mark.parametrize('tail', [b'\x01\x00', b'\xff' * 40])
def test_recovery_repairs_tail_before_accepting_more_writes(tmp_path, tail):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('first', 'one')
    # Stop without flushing, then append simulated crash debris.
    db._shutdown.set()
    db._bg.join()
    db._wal.close()
    with open(tmp_path / 'db' / 'wal.log', 'ab') as target:
        target.write(tail)
    db = LSMTree(path, sync_writes=False)
    assert db.get('first') == 'one'
    db.put('second', 'two')
    db._shutdown.set()
    db._bg.join()
    db._wal.close()
    with LSMTree(path) as recovered:
        assert recovered.get('first') == 'one'
        assert recovered.get('second') == 'two'
