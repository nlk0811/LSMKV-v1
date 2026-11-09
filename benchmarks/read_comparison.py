"""Compare lookup strategies on one SSTable, with identical hit/miss queries.

Run: python3 -m benchmarks.read_comparison
This measures warmed SSTable lookups, excluding reader-open and memtable costs.
"""
import argparse
import json
import platform
import random
import statistics
import tempfile
import time
from pathlib import Path

from lsm_engine.sstable import SSTableReader, SSTableWriter


def sequential_lookup(reader, target):
    """Sorted sequential scan; stop as soon as the target has been passed."""
    for key, value, tombstone in reader.scan():
        if key == target:
            return value, tombstone
        if key > target:
            break
    return None


def sparse_lookup(reader, target):
    """The same sparse-index lookup as get(), with no Bloom filter probe."""
    if not reader._index:
        return None
    _, offset, size = reader._index[reader._block_idx(target)]
    reader._f.seek(offset)
    return reader._search_block(reader._f.read(size), target)


def measure(reader, lookup, queries, value):
    latencies = []
    elapsed = 0
    for key, exists in queries:
        start = time.perf_counter_ns()
        result = lookup(reader, key)
        duration = time.perf_counter_ns() - start
        elapsed += duration
        latencies.append(duration / 1000)  # microseconds
        expected = (value, False) if exists else None
        if result != expected:
            raise AssertionError(f'Incorrect result for {key!r}: {result!r}')
    return elapsed / 1e9, latencies


def run(keys, queries_per_kind, repeats, seed):
    rng = random.Random(seed)
    value = b'x' * 128
    # Even keys are stored; odd keys are absent but within the same key range.
    queries = {
        'Present keys': [(f'key:{2*rng.randrange(keys):012d}'.encode(), True)
                         for _ in range(queries_per_kind)],
        'Missing keys': [(f'key:{2*rng.randrange(keys)+1:012d}'.encode(), False)
                         for _ in range(queries_per_kind)],
    }
    methods = [
        ('Sequential scan', sequential_lookup),
        ('Sparse index only', sparse_lookup),
        ('Bloom + sparse index', lambda reader, key: reader.get(key)),
    ]
    report = {
        'conditions': {'keys': keys, 'value_bytes': len(value),
                       'queries_per_kind': queries_per_kind, 'repeats': repeats,
                       'seed': seed, 'python': platform.python_version(),
                       'platform': platform.platform(),
                       'scope': 'Single open SSTable; warmed OS cache; excludes reader-open, memtable and multi-level search'},
        'results': {},
    }
    with tempfile.TemporaryDirectory(prefix='lsmkv_read_comparison_') as directory:
        path = str(Path(directory) / 'data.sst')
        writer = SSTableWriter(path, bloom_capacity=keys)
        for i in range(keys):
            writer.add(f'key:{2*i:012d}'.encode(), value)
        report['conditions']['sstable_bytes'] = writer.finish()
        reader = SSTableReader(path)
        try:
            # Read every block before measuring either approach.
            for _ in reader.scan():
                pass
            print(f'LSMKV retrieval comparison: {keys:,} records, 128-byte values')
            print(f'{queries_per_kind} queries per category, {repeats} repeats, seed={seed}')
            print('Same SSTable and same queries for every strategy. All answers checked.')
            for category, workload in queries.items():
                times = {name: [] for name, _ in methods}
                latencies = {name: [] for name, _ in methods}
                for repeat in range(repeats):
                    # Rotate the strategy order to reduce systematic ordering bias.
                    offset = repeat % len(methods)
                    for name, lookup in methods[offset:] + methods[:offset]:
                        elapsed, samples = measure(reader, lookup, workload, value)
                        times[name].append(elapsed)
                        latencies[name].extend(samples)
                baseline = statistics.median(times['Sequential scan'])
                rows = {}
                print(f'\n{category}')
                print(f'{"Strategy":<23} {"p50 (us)":>12} {"p95 (us)":>12} {"lookups/sec":>14} {"vs scan":>10}')
                for name, _ in methods:
                    ordered = sorted(latencies[name])
                    elapsed = statistics.median(times[name])
                    row = {
                        'p50_us': statistics.median(ordered),
                        'p95_us': ordered[min(len(ordered)-1, int(len(ordered)*0.95))],
                        'lookups_per_second': queries_per_kind / elapsed,
                        'speedup_vs_sequential': baseline / elapsed,
                        'elapsed_seconds_each_repeat': times[name],
                    }
                    rows[name] = row
                    print(f'{name:<23} {row["p50_us"]:>12.2f} {row["p95_us"]:>12.2f} '
                          f'{row["lookups_per_second"]:>14,.0f} {row["speedup_vs_sequential"]:>9.1f}x')
                report['results'][category] = rows
        finally:
            reader.close()
    print('\nScope: warmed single-SSTable lookup; reader metadata is already loaded.')
    print('This compares lookup strategies, not historical versions of the engine.')
    print('Full db.get() includes additional memtable, file-open and level-search costs.')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--keys', type=int, default=20_000)
    parser.add_argument('--queries', type=int, default=100, help='Queries per hit/miss category')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--output', type=Path, help='Optional JSON result file')
    args = parser.parse_args()
    if min(args.keys, args.queries, args.repeats) < 1:
        parser.error('--keys, --queries and --repeats must be positive')
    report = run(args.keys, args.queries, args.repeats, args.seed)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
        print(f'Results saved: {args.output.resolve()}')


if __name__ == '__main__':
    main()
