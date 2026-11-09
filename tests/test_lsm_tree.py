import os
import time
import shutil
import pytest
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lsm_engine.lsm_tree import LSMTree


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'))
    yield d
    d.close()
    shutil.rmtree(str(tmp_path / 'db'), ignore_errors=True)


def test_put_get(db):
    db.put('hello', 'world')
    assert db.get('hello') == 'world'

def test_get_missing(db):
    assert db.get('nope') is None

def test_overwrite(db):
    db.put('k', 'v1')
    db.put('k', 'v2')
    assert db.get('k') == 'v2'

def test_delete(db):
    db.put('k', 'v')
    db.delete('k')
    assert db.get('k') is None

def test_delete_missing(db):
    db.delete('ghost')
    assert db.get('ghost') is None

def test_scan_basic(db):
    for i in range(10):
        db.put(f'key{i:02d}', f'val{i}')
    results = list(db.scan('key03', 'key07'))
    assert [k for k, _ in results] == [f'key{i:02d}' for i in range(3, 7)]

def test_scan_tombstone_excluded(db):
    db.put('a', '1')
    db.put('b', '2')
    db.delete('a')
    keys = [k for k, _ in db.scan()]
    assert 'a' not in keys
    assert 'b' in keys

def test_persistence_across_flush(tmp_path):
    path = str(tmp_path / 'db2')
    db = LSMTree(path)
    for i in range(200):
        db.put(f'persistent{i:04d}', f'v{i}')
    db.close()

    db2 = LSMTree(path)
    for i in range(200):
        assert db2.get(f'persistent{i:04d}') == f'v{i}'
    db2.close()
    shutil.rmtree(path, ignore_errors=True)

def test_stats_returns_dict(db):
    db.put('x', 'y')
    s = db.stats()
    assert 'memtable_bytes' in s
    assert 'levels' in s

def test_large_write_read(db):
    n = 2000
    for i in range(n):
        db.put(f'key{i:07d}', f'val{i}')
    for i in range(n):
        assert db.get(f'key{i:07d}') == f'val{i}'

def test_write_delete_rescan(db):
    for i in range(50):
        db.put(f'item{i:03d}', 'present')
    for i in range(0, 50, 2):
        db.delete(f'item{i:03d}')
    survivors = [k for k, _ in db.scan()]
    for i in range(0, 50, 2):
        assert f'item{i:03d}' not in survivors
    for i in range(1, 50, 2):
        assert f'item{i:03d}' in survivors
