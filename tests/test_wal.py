import os
import tempfile
import struct
import pytest
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lsm_engine.wal import WAL, OpType


@pytest.fixture
def wal_path(tmp_path):
    return str(tmp_path / 'test.wal')


def test_log_put_and_replay(wal_path):
    w = WAL(wal_path)
    w.log_put(b'hello', b'world')
    w.close()

    records = list(WAL(wal_path).replay())
    assert len(records) == 1
    op, k, v = records[0]
    assert op == OpType.PUT
    assert k == b'hello'
    assert v == b'world'

def test_log_delete_and_replay(wal_path):
    w = WAL(wal_path)
    w.log_delete(b'gone')
    w.close()

    records = list(WAL(wal_path).replay())
    assert len(records) == 1
    op, k, v = records[0]
    assert op == OpType.DELETE
    assert k == b'gone'

def test_multiple_records(wal_path):
    w = WAL(wal_path)
    for i in range(100):
        w.log_put(f'k{i}'.encode(), f'v{i}'.encode())
    w.close()

    records = list(WAL(wal_path).replay())
    assert len(records) == 100

def test_truncate_clears_wal(wal_path):
    w = WAL(wal_path)
    w.log_put(b'x', b'y')
    w.truncate()
    records = list(w.replay())
    w.close()
    assert records == []

def test_replay_stops_on_truncated_record(wal_path):
    w = WAL(wal_path)
    w.log_put(b'good', b'record')
    w.close()

    # Corrupt the file by appending a partial header
    with open(wal_path, 'ab') as f:
        f.write(b'\x01\x00\x00\x00')   # partial header, no key/value/crc

    records = list(WAL(wal_path).replay())
    assert len(records) == 1            # only the good record

def test_replay_stops_on_bad_crc(wal_path):
    w = WAL(wal_path)
    w.log_put(b'key', b'val')
    w.close()

    # Flip a byte in the middle of the file to corrupt CRC
    with open(wal_path, 'r+b') as f:
        f.seek(5)
        b = f.read(1)
        f.seek(5)
        f.write(bytes([b[0] ^ 0xFF]))

    records = list(WAL(wal_path).replay())
    assert len(records) == 0

def test_wal_size_bytes(wal_path):
    w = WAL(wal_path)
    assert w.size_bytes == 0
    w.log_put(b'k', b'v')
    assert w.size_bytes > 0
    w.close()

def test_write_after_truncate(wal_path):
    w = WAL(wal_path)
    w.log_put(b'before', b'truncate')
    w.truncate()
    w.log_put(b'after', b'truncate')
    records = list(w.replay())
    w.close()
    assert len(records) == 1
    assert records[0][1] == b'after'
