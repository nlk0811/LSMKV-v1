"""The point-read path reuses open SSTable readers and drops them on compaction."""
import os

from lsm_engine.lsm_tree import LSMTree


def _open_without_background_thread(path):
    db = LSMTree(str(path), sync_writes=False)
    db._shutdown.set()   # keep the test deterministic: compact by hand
    db._bg.join()
    return db


def _flush(db):
    with db._write_lock:
        db._flush_memtable()


def test_readers_are_reused_between_gets(tmp_path):
    db = _open_without_background_thread(tmp_path)
    for i in range(50):
        db.put(f'k{i:03d}', f'v{i}')
    _flush(db)
    assert db.get('k010') == 'v10'
    cached = dict(db._readers)
    assert cached
    assert db.get('k020') == 'v20'
    assert db.get('absent') is None
    for path, reader in cached.items():
        assert db._readers[path] is reader
    db.close()


def test_readers_dropped_when_compaction_deletes_files(tmp_path):
    db = _open_without_background_thread(tmp_path)
    for batch in range(3):
        for i in range(50):
            db.put(f'k{i:03d}', f'v{batch}-{i}')
        _flush(db)
    for i in range(0, 50, 5):
        db.delete(f'k{i:03d}')
    _flush(db)

    # Touch every L0 file so each one has a cached reader.
    assert db.get('k001') == 'v2-1'
    assert db.get('k000') is None
    assert db.get('absent') is None
    old_files = list(db._manifest.all_files())
    assert len(db._readers) >= 1

    db._compact_into(0)

    for path in old_files:
        assert path not in db._readers
        assert not os.path.exists(path)
    assert db.get('k001') == 'v2-1'
    assert db.get('k006') == 'v2-6'
    assert db.get('k000') is None
    assert db.get('absent') is None
    db.close()
    assert db._readers == {}
