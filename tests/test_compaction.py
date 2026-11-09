import os
import pytest
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lsm_engine.sstable  import SSTableWriter, SSTableReader
from lsm_engine.compaction import compact, kway_merge


def _make_sst(path, entries):
    w = SSTableWriter(path, bloom_capacity=max(len(entries), 10))
    for k, v, t in entries:
        w.add(k, v, tombstone=t)
    w.finish()


def test_basic_merge(tmp_path):
    p1 = str(tmp_path / 'a.sst')
    p2 = str(tmp_path / 'b.sst')
    out = str(tmp_path / 'out.sst')

    _make_sst(p1, [(b'b', b'B', False), (b'd', b'D', False)])
    _make_sst(p2, [(b'a', b'A', False), (b'c', b'C', False)])

    n = compact([p1, p2], out)  # p1 is newer
    assert n == 4

    r = SSTableReader(out)
    result = [(k, v) for k, v, _ in r.scan()]
    r.close()
    assert result == [(b'a', b'A'), (b'b', b'B'), (b'c', b'C'), (b'd', b'D')]


def test_deduplication_newer_wins(tmp_path):
    newer = str(tmp_path / 'newer.sst')
    older = str(tmp_path / 'older.sst')
    out   = str(tmp_path / 'out.sst')

    _make_sst(newer, [(b'key', b'new_val', False)])
    _make_sst(older, [(b'key', b'old_val', False)])

    compact([newer, older], out)

    r = SSTableReader(out)
    val, tomb = r.get(b'key')
    r.close()
    assert val == b'new_val'
    assert not tomb


def test_tombstone_preserved(tmp_path):
    p   = str(tmp_path / 'a.sst')
    out = str(tmp_path / 'out.sst')
    _make_sst(p, [(b'gone', b'', True), (b'stay', b'v', False)])
    compact([p], out, drop_tombstones=False)

    r = SSTableReader(out)
    _, tomb = r.get(b'gone')
    r.close()
    assert tomb is True


def test_tombstone_dropped_at_deepest(tmp_path):
    p   = str(tmp_path / 'a.sst')
    out = str(tmp_path / 'out.sst')
    _make_sst(p, [(b'gone', b'', True), (b'stay', b'v', False)])
    compact([p], out, drop_tombstones=True)

    r = SSTableReader(out)
    assert r.get(b'gone') is None
    assert r.get(b'stay') == (b'v', False)
    r.close()


def test_empty_inputs_returns_zero(tmp_path):
    out = str(tmp_path / 'out.sst')
    n   = compact([], out)
    assert n == 0
    assert not os.path.exists(out)


def test_large_merge(tmp_path):
    paths = []
    for i in range(4):
        p = str(tmp_path / f'sst{i}.sst')
        entries = [(f'k{j:06d}'.encode(), f'v{i}'.encode(), False) for j in range(i, 200, 4)]
        _make_sst(p, entries)
        paths.append(p)

    out = str(tmp_path / 'merged.sst')
    n   = compact(paths, out)
    assert n == 200

    r = SSTableReader(out)
    keys = [k for k, _, _ in r.scan()]
    r.close()
    assert keys == sorted(keys)
    assert len(keys) == 200
