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


def test_lru_eviction_preserves_live_scan_and_releases_resources(tmp_path):
    from lsm_engine.lsm_tree import READER_CACHE_LIMIT
    from lsm_engine.sstable import SSTableWriter

    db = _open_without_background_thread(tmp_path)
    paths = []
    for i in range(READER_CACHE_LIMIT + 8):
        path = str(tmp_path / f'L0_test_{i}.sst')
        writer = SSTableWriter(path)
        writer.add(f'k{i:03}'.encode(), b'value')
        writer.finish()
        paths.append(path)
    db._manifest.apply_compaction({}, {0: paths})
    try:
        scan = db.scan()
        assert next(scan) == ('k000', 'value')
        assert db.get('absent') is None
        assert list(scan) == [(f'k{i:03}', 'value') for i in range(1, len(paths))]
        assert not db._reader_pins
        assert len(db._readers) <= READER_CACHE_LIMIT
        for i in range(len(paths)):
            assert db.get(f'k{i:03}') == 'value'
        assert len(db._readers) <= READER_CACHE_LIMIT
        readers = list(db._readers.values())
    finally:
        db.close()
    assert all(reader._mm is None for reader in readers)


def test_many_tables_under_descriptor_limit(tmp_path):
    """A missing get and a scan must work without retaining duplicate handles."""
    import subprocess
    import sys
    import textwrap

    import pytest
    pytest.importorskip('resource')
    script = textwrap.dedent('''
        import os, resource, sys
        from lsm_engine import LSMTree
        from lsm_engine.sstable import SSTableWriter
        db = LSMTree(sys.argv[1], sync_writes=False)
        db._shutdown.set()
        db._bg.join()
        paths = []
        for i in range(80):
            path = os.path.join(sys.argv[1], f'L0_{i}.sst')
            writer = SSTableWriter(path)
            writer.add(f'k{i:03}'.encode(), b'value')
            writer.finish()
            paths.append(path)
        db._manifest.apply_compaction({}, {0: paths})
        original = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (min(128, original[0]), original[1]))
        try:
            assert db.get('zzmissing') is None
            assert list(db.scan()) == [(f'k{i:03}', 'value') for i in range(80)]
            assert db.get('k000') == 'value'
        finally:
            resource.setrlimit(resource.RLIMIT_NOFILE, original)
            db.close()
    ''')
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
