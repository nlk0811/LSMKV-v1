import random
import pytest
from lsm_engine import LSMTree


def opened(path):
    db = LSMTree(str(path), sync_writes=False)
    db._shutdown.set()
    db._bg.join()
    return db


def test_existing_bytes_api(tmp_path):
    with opened(tmp_path) as db:
        db.put(b'key', b'value')
        assert db.get(b'key') == 'value'
        assert list(db.scan(b'a', b'z')) == [('key', 'value')]


@pytest.mark.parametrize('sources', [1, 2, 5])
def test_randomized_merge_and_reopen(tmp_path, sources):
    rng = random.Random(52)
    expected = {}
    with opened(tmp_path) as db:
        for batch in range(sources):
            for i in range(300):
                key = f'k{rng.randrange(150):03}'
                if rng.random() < .3:
                    db.delete(key)
                    expected.pop(key, None)
                else:
                    value = f'v{batch}-{i}'
                    db.put(key, value)
                    expected[key] = value
            if batch < sources - 1:
                db._flush_memtable()
        assert list(db.scan()) == sorted(expected.items())
        assert list(db.scan('k030', 'k100')) == sorted((k,v) for k,v in expected.items() if 'k030' <= k < 'k100')
        db._flush_memtable()
        db._compact_into(0)
        assert list(db.scan()) == sorted(expected.items())
    with opened(tmp_path) as db:
        assert list(db.scan()) == sorted(expected.items())
        for k,v in expected.items():
            assert db.get(k) == v


def test_suspended_scans_survive_compaction_and_close(tmp_path):
    db = opened(tmp_path)
    for batch in range(3):
        for i in range(50):
            db.put(f'k{i:03}', f'v{batch}')
        db._flush_memtable()
    expected = [(f'k{i:03}', 'v2') for i in range(50)]
    first, second = db.scan(), db.scan()
    assert next(first) == expected[0]
    assert next(second) == expected[0]
    db._compact_into(0)
    db.close()
    assert list(first) == expected[1:]
    assert list(second) == expected[1:]
    if hasattr(db, '_reader_pins'):
        assert db._reader_pins == {}
        assert db._retired_readers == {}


def test_early_scan_close_releases_pins(tmp_path):
    with opened(tmp_path) as db:
        for i in range(5):
            db.put(str(i), str(i))
        db._flush_memtable()
        scan = db.scan()
        next(scan)
        scan.close()
        if hasattr(db, '_reader_pins'):
            assert not db._reader_pins
