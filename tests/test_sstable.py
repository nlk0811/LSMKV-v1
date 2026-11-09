import os
import pytest
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lsm_engine.sstable import SSTableWriter, SSTableReader


@pytest.fixture
def sst_path(tmp_path):
    return str(tmp_path / 'test.sst')


def _build(path, entries):
    """entries: list of (key, value) or (key, value, tombstone)"""
    w = SSTableWriter(path, bloom_capacity=max(len(entries), 10))
    for e in entries:
        if len(e) == 3:
            w.add(e[0], e[1], tombstone=e[2])
        else:
            w.add(e[0], e[1])
    w.finish()
    return SSTableReader(path)


def test_get_existing(sst_path):
    r = _build(sst_path, [(b'a', b'1'), (b'b', b'2'), (b'c', b'3')])
    assert r.get(b'b') == (b'2', False)
    r.close()

def test_get_missing(sst_path):
    r = _build(sst_path, [(b'a', b'1'), (b'c', b'3')])
    assert r.get(b'b') is None
    r.close()

def test_get_tombstone(sst_path):
    r = _build(sst_path, [(b'dead', b'', True)])
    val, tomb = r.get(b'dead')
    assert tomb is True
    r.close()

def test_bloom_filter_rejects_missing(sst_path):
    r = _build(sst_path, [(b'x', b'1')])
    assert not r.may_contain(b'definitely_not_here_zzzz')
    r.close()

def test_scan_all(sst_path):
    entries = [(f'k{i:03d}'.encode(), f'v{i}'.encode()) for i in range(50)]
    r = _build(sst_path, entries)
    result = [(k, v) for k, v, _ in r.scan()]
    assert result == [(k, v) for k, v in entries]
    r.close()

def test_scan_range(sst_path):
    entries = [(f'k{i:03d}'.encode(), b'v') for i in range(20)]
    r = _build(sst_path, entries)
    result = [k for k, _, _ in r.scan(start=b'k005', end=b'k010')]
    assert result == [f'k{i:03d}'.encode() for i in range(5, 10)]
    r.close()

def test_large_sstable_multiblock(sst_path):
    # Force multiple blocks (BLOCK_SIZE = 4096)
    entries = [(f'key{i:06d}'.encode(), b'x' * 100) for i in range(200)]
    r = _build(sst_path, entries)
    for i in range(200):
        assert r.get(f'key{i:06d}'.encode()) == (b'x' * 100, False)
    r.close()

def test_first_key(sst_path):
    entries = [(b'alpha', b'1'), (b'beta', b'2'), (b'gamma', b'3')]
    r = _build(sst_path, entries)
    assert r.first_key == b'alpha'
    r.close()

def test_bad_magic_raises(tmp_path):
    path = str(tmp_path / 'bad.sst')
    with open(path, 'wb') as f:
        f.write(b'\x00' * 64)
    with pytest.raises(ValueError, match='magic'):
        SSTableReader(path)

def test_empty_sstable_no_crash(sst_path):
    # Writing zero entries should not crash
    w = SSTableWriter(sst_path, bloom_capacity=10)
    size = w.finish()
    # File exists with just index + bloom + footer
    assert os.path.exists(sst_path)
