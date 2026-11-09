import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lsm_engine.bloom_filter import BloomFilter


def test_membership():
    bf = BloomFilter(1000)
    keys = [f'key{i}'.encode() for i in range(500)]
    for k in keys:
        bf.add(k)
    for k in keys:
        assert bf.may_contain(k), f'{k} should be in filter'

def test_no_false_negatives():
    bf = BloomFilter(500)
    for i in range(200):
        bf.add(f'item{i}'.encode())
    for i in range(200):
        assert bf.may_contain(f'item{i}'.encode())

def test_false_positive_rate():
    n    = 10_000
    fpr  = 0.01
    bf   = BloomFilter(n, fpr)
    for i in range(n):
        bf.add(f'present{i}'.encode())
    misses = [f'absent{i}'.encode() for i in range(5_000)]
    fp = sum(1 for k in misses if bf.may_contain(k))
    actual_fpr = fp / len(misses)
    # Allow 3× headroom over target FPR
    assert actual_fpr < fpr * 3, f'FPR too high: {actual_fpr:.4f}'

def test_serialize_deserialize_roundtrip():
    bf = BloomFilter(1000)
    keys = [f'word{i}'.encode() for i in range(200)]
    for k in keys:
        bf.add(k)
    data = bf.serialize()
    bf2  = BloomFilter.deserialize(data)
    for k in keys:
        assert bf2.may_contain(k)

def test_serialize_preserves_fpr():
    bf  = BloomFilter(500, false_positive_rate=0.05)
    bf2 = BloomFilter.deserialize(bf.serialize())
    assert abs(bf2.fpr - 0.05) < 1e-5

def test_empty_filter():
    bf = BloomFilter(100)
    assert not bf.may_contain(b'anything')

def test_optimal_hash_count():
    bf = BloomFilter(1000, 0.01)
    # Optimal k for FPR=1% is ~7
    assert 5 <= bf.hash_count <= 10

def test_serialization_size_reasonable():
    bf   = BloomFilter(10_000, 0.01)
    data = bf.serialize()
    # ~10 bits/key for FPR=1% → ~12.5 KB for 10k keys + small header
    assert len(data) < 20_000
