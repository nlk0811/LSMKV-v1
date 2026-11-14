"""Verify benchmark conditions, so reported measurements describe the data."""
import tempfile

import pytest

from benchmarks.sqlite_comparison import (
    load_for_reads, make_dataset, run_point_reads,
)


@pytest.mark.parametrize('size', [1, 10, 128])
def test_dataset_values_have_requested_size(size):
    assert {len(v) for v in make_dataset(100, size, 42).values()} == {size}


def test_both_kinds_of_missing_read_are_verified():
    dataset = make_dataset(100, 32, 42)
    with tempfile.TemporaryDirectory() as root:
        engines = load_for_reads(root, dataset, 42)
        try:
            results = run_point_reads(engines, dataset, 30, 42)
            assert set(results) == {'present', 'missing', 'in_range_missing'}
            for kind in results.values():
                assert all(stats['ops'] == 30 for stats in kind.values())
        finally:
            for engine in engines.values():
                engine.close()
