"""LSMKV vs SQLite comparison benchmark.

Both engines store the same keys and values and are asked the same questions.
Every answer is checked against a plain Python dict, so a fast but wrong
result fails the run instead of looking good in a table.

Workloads
---------
1. Durable writes  : one fsync'd commit per put (LSMKV sync_writes=True,
                     SQLite journal_mode=WAL + synchronous=FULL).
2. Bulk writes     : one commit per put, no fsync (LSMKV sync_writes=False,
                     SQLite synchronous=OFF), in sequential and random order.
3. Point reads     : present keys and missing keys, full db.get() path.
4. Range scans     : many short [start, end) scans.
5. Full scan       : every key in order.
6. Disk usage      : bytes on disk after the load is closed.

Reads run after the database is closed and reopened, so LSMKV reads come from
SSTables and not just the in-memory memtable.

Usage:
    python3 -m benchmarks.sqlite_comparison
    python3 -m benchmarks.sqlite_comparison --keys 100000 --output benchmark_results/sqlite_comparison.json
"""
import argparse
import json
import os
import platform
import random
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lsm_engine.lsm_tree import LSMTree  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────

def make_dataset(count, value_bytes, seed):
    return {f'user{i:09d}'.encode():
            (f'v{i:09d}'.encode() + b'x' * value_bytes)[:value_bytes]
            for i in range(count)}


def as_bytes(value):
    return value.encode() if isinstance(value, str) else value


def percentile(sorted_values, fraction):
    index = min(len(sorted_values) - 1, int(len(sorted_values) * fraction))
    return sorted_values[index]


def latency_stats(samples):
    ordered = sorted(samples)
    total = sum(ordered)
    return {
        'ops': len(ordered),
        'ops_per_second': len(ordered) / total if total else float('inf'),
        'p50_us': percentile(ordered, 0.50) * 1e6,
        'p95_us': percentile(ordered, 0.95) * 1e6,
        'p99_us': percentile(ordered, 0.99) * 1e6,
        'mean_us': statistics.fmean(ordered) * 1e6,
    }


def dir_bytes(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            total += os.path.getsize(os.path.join(root, name))
    return total


# ── engine adapters ────────────────────────────────────────────────────────

class LSMKVEngine:
    name = 'LSMKV'

    def __init__(self, directory, durable):
        self.directory = directory
        self.durable = durable
        self.db = LSMTree(directory, sync_writes=durable)

    def put(self, key, value):
        self.db.put(key, value)

    def get(self, key):
        result = self.db.get(key)
        return None if result is None else as_bytes(result)

    def scan(self, start, end):
        return [(as_bytes(k), as_bytes(v)) for k, v in self.db.scan(start, end)]

    def reopen(self):
        self.db.close()
        self.db = LSMTree(self.directory, sync_writes=self.durable)

    def close(self):
        self.db.close()

    def disk_bytes(self):
        return dir_bytes(self.directory)


class SQLiteEngine:
    name = 'SQLite'

    def __init__(self, directory, durable):
        self.directory = directory
        self.path = os.path.join(directory, 'bench.db')
        self.durable = durable
        self.conn = self._connect()
        self.conn.execute('CREATE TABLE IF NOT EXISTS kv '
                          '(key BLOB PRIMARY KEY, value BLOB) WITHOUT ROWID')
        self.conn.commit()

    def _connect(self):
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=%s' % ('FULL' if self.durable else 'OFF'))
        if self.durable:
            # LSMKV uses F_FULLFSYNC on macOS (flush to the physical disk).
            # SQLite defaults to a plain fsync there, which is weaker, so ask
            # for the same guarantee to keep the comparison fair. These
            # pragmas are ignored on other platforms.
            conn.execute('PRAGMA fullfsync=ON')
            conn.execute('PRAGMA checkpoint_fullfsync=ON')
        return conn

    def put(self, key, value):
        # Autocommit: every put is its own transaction, like one LSMKV put.
        self.conn.execute('INSERT OR REPLACE INTO kv VALUES (?, ?)', (key, value))

    def get(self, key):
        row = self.conn.execute('SELECT value FROM kv WHERE key = ?', (key,)).fetchone()
        return None if row is None else bytes(row[0])

    def scan(self, start, end):
        rows = self.conn.execute(
            'SELECT key, value FROM kv WHERE key >= ? AND key < ? ORDER BY key',
            (start, end)).fetchall()
        return [(bytes(k), bytes(v)) for k, v in rows]

    def reopen(self):
        self.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.conn.close()
        self.conn = self._connect()

    def close(self):
        self.conn.close()

    def disk_bytes(self):
        return dir_bytes(self.directory)


ENGINES = (LSMKVEngine, SQLiteEngine)


# ── workloads ──────────────────────────────────────────────────────────────

def timed_writes(engine, items):
    samples = []
    for key, value in items:
        started = time.perf_counter()
        engine.put(key, value)
        samples.append(time.perf_counter() - started)
    return samples


def run_durable_writes(root, dataset, count, seed):
    rng = random.Random(seed)
    items = rng.sample(sorted(dataset.items()), count)
    results = {}
    for cls in ENGINES:
        directory = tempfile.mkdtemp(dir=root)
        engine = cls(directory, durable=True)
        try:
            results[cls.name] = latency_stats(timed_writes(engine, items))
        finally:
            engine.close()
    return results


def run_bulk_writes(root, dataset, order, seed):
    items = sorted(dataset.items())
    if order == 'random':
        random.Random(seed).shuffle(items)
    results = {}
    for cls in ENGINES:
        directory = tempfile.mkdtemp(dir=root)
        engine = cls(directory, durable=False)
        try:
            started = time.perf_counter()
            for key, value in items:
                engine.put(key, value)
            elapsed = time.perf_counter() - started
            results[cls.name] = {'ops': len(items), 'seconds': elapsed,
                                 'ops_per_second': len(items) / elapsed}
        finally:
            engine.close()
    return results


def load_for_reads(root, dataset, seed):
    """Load both engines in random order, then reopen so reads hit disk."""
    items = sorted(dataset.items())
    random.Random(seed).shuffle(items)
    engines = {}
    for cls in ENGINES:
        directory = tempfile.mkdtemp(dir=root)
        engine = cls(directory, durable=False)
        for key, value in items:
            engine.put(key, value)
        engine.reopen()
        engines[cls.name] = engine
    return engines


def run_point_reads(engines, dataset, queries, seed):
    rng = random.Random(seed)
    present = rng.sample(sorted(dataset), queries)
    missing = [f'zzmissing{i:09d}'.encode() for i in range(queries)]
    # Suffix an existing key below the maximum: absent but inside the range.
    interior = sorted(dataset)[:-1]
    in_range = [rng.choice(interior) + b'!' for _ in range(queries)]
    results = {}
    for kind, keys in (('present', present), ('missing', missing),
                       ('in_range_missing', in_range)):
        results[kind] = {}
        for name, engine in engines.items():
            samples = []
            for key in keys:
                started = time.perf_counter()
                got = engine.get(key)
                samples.append(time.perf_counter() - started)
                if got != dataset.get(key):
                    raise AssertionError(f'{name} returned a wrong answer for {key!r}')
            results[kind][name] = latency_stats(samples)
    return results


def run_range_scans(engines, dataset, scans, width, seed):
    rng = random.Random(seed)
    ordered = sorted(dataset)
    starts = [rng.randrange(0, len(ordered) - width) for _ in range(scans)]
    results = {}
    for name, engine in engines.items():
        samples = []
        for first in starts:
            start, end = ordered[first], ordered[first + width]
            expected = [(k, dataset[k]) for k in ordered[first:first + width]]
            began = time.perf_counter()
            got = engine.scan(start, end)
            samples.append(time.perf_counter() - began)
            if got != expected:
                raise AssertionError(f'{name} returned a wrong range scan')
        stats = latency_stats(samples)
        stats['rows_per_scan'] = width
        results[name] = stats
    return results


def run_full_scan(engines, dataset):
    ordered = sorted(dataset)
    expected = [(k, dataset[k]) for k in ordered]
    low, high = ordered[0], ordered[-1] + b'\xff'
    results = {}
    for name, engine in engines.items():
        started = time.perf_counter()
        got = engine.scan(low, high)
        elapsed = time.perf_counter() - started
        if got != expected:
            raise AssertionError(f'{name} returned a wrong full scan')
        results[name] = {'rows': len(got), 'seconds': elapsed,
                         'rows_per_second': len(got) / elapsed}
    return results


# ── report ─────────────────────────────────────────────────────────────────

def ratio(lsm, sql, higher_is_better=True):
    """How many times better LSMKV is than SQLite (<1 means SQLite wins)."""
    if higher_is_better:
        return lsm / sql
    return sql / lsm


def print_report(report):
    c = report['conditions']
    print(f"\nLSMKV vs SQLite  |  {c['keys']:,} keys, {c['value_bytes']} B values, "
          f"python {c['python']}, {c['platform']}")
    print('Ratio = LSMKV relative to SQLite; above 1.0x means LSMKV is faster.\n')

    rows = []
    d = report['durable_writes']
    rows.append(('Durable write (fsync each put)',
                 f"{d['LSMKV']['ops_per_second']:,.0f} ops/s",
                 f"{d['SQLite']['ops_per_second']:,.0f} ops/s",
                 ratio(d['LSMKV']['ops_per_second'], d['SQLite']['ops_per_second'])))
    for order in ('sequential', 'random'):
        b = report['bulk_writes'][order]
        rows.append((f'Bulk write, {order} order',
                     f"{b['LSMKV']['ops_per_second']:,.0f} ops/s",
                     f"{b['SQLite']['ops_per_second']:,.0f} ops/s",
                     ratio(b['LSMKV']['ops_per_second'], b['SQLite']['ops_per_second'])))
    for kind in ('present', 'missing', 'in_range_missing'):
        r = report['point_reads'][kind]
        rows.append((f'Point read, {kind} key (p50)',
                     f"{r['LSMKV']['p50_us']:,.1f} us",
                     f"{r['SQLite']['p50_us']:,.1f} us",
                     ratio(r['LSMKV']['p50_us'], r['SQLite']['p50_us'], False)))
    s = report['range_scans']
    rows.append((f"Range scan, {s['LSMKV']['rows_per_scan']} rows (p50)",
                 f"{s['LSMKV']['p50_us']:,.1f} us",
                 f"{s['SQLite']['p50_us']:,.1f} us",
                 ratio(s['LSMKV']['p50_us'], s['SQLite']['p50_us'], False)))
    f = report['full_scan']
    rows.append(('Full scan',
                 f"{f['LSMKV']['rows_per_second']:,.0f} rows/s",
                 f"{f['SQLite']['rows_per_second']:,.0f} rows/s",
                 ratio(f['LSMKV']['rows_per_second'], f['SQLite']['rows_per_second'])))
    disk = report['disk_bytes']
    rows.append(('Disk usage after load',
                 f"{disk['LSMKV'] / 1e6:,.2f} MB",
                 f"{disk['SQLite'] / 1e6:,.2f} MB",
                 ratio(disk['LSMKV'], disk['SQLite'], False)))

    width = max(len(row[0]) for row in rows)
    print(f"{'Workload'.ljust(width)}  {'LSMKV':>16}  {'SQLite':>16}  {'Ratio':>8}")
    print('-' * (width + 46))
    for label, left, right, rel in rows:
        print(f'{label.ljust(width)}  {left:>16}  {right:>16}  {rel:>7.2f}x')
    print('\nAll answers were verified against a Python dict.')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--keys', type=int, default=50_000)
    parser.add_argument('--value-bytes', type=int, default=128)
    parser.add_argument('--durable-writes', type=int, default=1_000,
                        help='number of fsync-per-put writes (slow by design)')
    parser.add_argument('--queries', type=int, default=2_000)
    parser.add_argument('--scans', type=int, default=200)
    parser.add_argument('--scan-width', type=int, default=100)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--output', help='write the full JSON report here')
    args = parser.parse_args()

    if min(args.keys, args.value_bytes, args.durable_writes, args.queries,
           args.scans, args.scan_width) < 1:
        parser.error('workload sizes must be positive')
    if args.queries > args.keys:
        parser.error('--queries must not exceed --keys')
    if args.keys < max(args.durable_writes, args.scan_width * 2, 100):
        parser.error('--keys must exceed the durable write and scan widths')

    dataset = make_dataset(args.keys, args.value_bytes, args.seed)
    root = tempfile.mkdtemp(prefix='lsmkv_vs_sqlite_')
    engines = {}
    try:
        print('Durable writes...', flush=True)
        durable = run_durable_writes(root, dataset, args.durable_writes, args.seed)
        print('Bulk writes (sequential, random)...', flush=True)
        bulk = {order: run_bulk_writes(root, dataset, order, args.seed)
                for order in ('sequential', 'random')}
        print('Loading for reads...', flush=True)
        engines = load_for_reads(root, dataset, args.seed)
        disk = {name: engine.disk_bytes() for name, engine in engines.items()}
        print('Point reads...', flush=True)
        point = run_point_reads(engines, dataset, args.queries, args.seed)
        print('Range scans...', flush=True)
        scans = run_range_scans(engines, dataset, args.scans, args.scan_width, args.seed)
        print('Full scan...', flush=True)
        full = run_full_scan(engines, dataset)
    finally:
        for engine in engines.values():
            engine.close()
        shutil.rmtree(root, ignore_errors=True)

    report = {
        'conditions': {
            'keys': args.keys,
            'value_bytes': args.value_bytes,
            'durable_writes': args.durable_writes,
            'queries_per_kind': args.queries,
            'scans': args.scans,
            'scan_width': args.scan_width,
            'seed': args.seed,
            'python': platform.python_version(),
            'sqlite': sqlite3.sqlite_version,
            'platform': platform.platform(),
            'notes': ('LSMKV reads come from SSTables after a close/reopen. '
                      'SQLite uses WAL, WITHOUT ROWID, one autocommit transaction '
                      'per put. Single run; treat ratios as indicative.'),
        },
        'durable_writes': durable,
        'bulk_writes': bulk,
        'point_reads': point,
        'range_scans': scans,
        'full_scan': full,
        'disk_bytes': disk,
    }
    print_report(report)
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, 'w') as target:
            json.dump(report, target, indent=2)
        print(f'Saved {args.output}')


if __name__ == '__main__':
    main()
