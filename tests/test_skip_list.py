import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lsm_engine.skip_list import SkipList


def test_put_and_get():
    sl = SkipList()
    sl.put(b'hello', b'world')
    assert sl.get(b'hello') == (b'world', False)

def test_get_missing():
    sl = SkipList()
    assert sl.get(b'nope') is None

def test_overwrite():
    sl = SkipList()
    sl.put(b'k', b'v1')
    sl.put(b'k', b'v2')
    assert sl.get(b'k') == (b'v2', False)

def test_delete_existing():
    sl = SkipList()
    sl.put(b'k', b'v')
    sl.delete(b'k')
    val, deleted = sl.get(b'k')
    assert deleted is True
    assert val is None

def test_delete_missing_inserts_tombstone():
    sl = SkipList()
    sl.delete(b'ghost')
    r = sl.get(b'ghost')
    assert r is not None
    _, deleted = r
    assert deleted is True

def test_put_after_delete():
    sl = SkipList()
    sl.put(b'k', b'v1')
    sl.delete(b'k')
    sl.put(b'k', b'v2')
    assert sl.get(b'k') == (b'v2', False)

def test_sorted_order():
    sl = SkipList()
    keys = [b'dog', b'ant', b'cat', b'bee']
    for k in keys:
        sl.put(k, k)
    result = [k for k, _, _ in sl.scan()]
    assert result == sorted(keys)

def test_scan_range():
    sl = SkipList()
    for i in range(10):
        sl.put(f'{i:02d}'.encode(), b'v')
    result = [k for k, _, _ in sl.scan(start=b'03', end=b'07')]
    assert result == [b'03', b'04', b'05', b'06']

def test_scan_includes_tombstones():
    sl = SkipList()
    sl.put(b'a', b'v')
    sl.delete(b'b')
    entries = list(sl.scan())
    assert any(d for _, _, d in entries)

def test_len():
    sl = SkipList()
    for i in range(5):
        sl.put(str(i).encode(), b'x')
    assert len(sl) == 5

def test_size_bytes_increases_on_put():
    sl = SkipList()
    sl.put(b'key', b'value')
    assert sl.size_bytes > 0

def test_size_bytes_decreases_on_delete():
    sl = SkipList()
    sl.put(b'key', b'value')
    before = sl.size_bytes
    sl.delete(b'key')
    assert sl.size_bytes < before

def test_large_insertion():
    sl = SkipList()
    n = 5000
    for i in range(n):
        sl.put(f'key{i:06d}'.encode(), f'val{i}'.encode())
    for i in range(n):
        r = sl.get(f'key{i:06d}'.encode())
        assert r is not None
        val, deleted = r
        assert not deleted
        assert val == f'val{i}'.encode()

def test_iter_equals_scan():
    sl = SkipList()
    for i in range(20):
        sl.put(f'{i:02d}'.encode(), b'v')
    assert list(iter(sl)) == list(sl.scan())
