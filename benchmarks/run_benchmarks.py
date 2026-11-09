"""
Benchmarks
==========
Measures write throughput, read latency (p50/p99), range scan throughput,
and Bloom filter false-positive rate.

Run:
    python3 -m benchmarks.run_benchmarks
"""

import os
import random
import shutil
import string
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lsm_engine.lsm_tree    import LSMTree
from lsm_engine.bloom_filter import BloomFilter

DB_DIR  = '/tmp/lsm_bench_db'
N_WRITE = 50_000
N_READ  = 10_000
N_SCAN  = 500


def rk(n=16) -> str:
    return ''.join(random.choices(string.ascii_lowercase, k=n))

def rv(n=128) -> str:
    return ''.join(random.choices(string.ascii_letters, k=n))


# ── Bloom filter FPR ──────────────────────────────────────────────────────────

def bench_bloom_fpr(n=100_000, target_fpr=0.01):
    bf   = BloomFilter(n, target_fpr)
    keys = [rk(20).encode() for _ in range(n)]
    for k in keys:
        bf.add(k)

    probes  = [rk(21).encode() for _ in range(10_000)]
    fp      = sum(1 for k in probes if bf.may_contain(k))
    actual  = fp / len(probes)
    bits_per_key = (bf.bit_count / n)
    print(f'  Bloom FPR  target={target_fpr:.1%}  actual={actual:.3%}  '
          f'bits/key={bits_per_key:.1f}  hash_fns={bf.hash_count}')


# ── Write throughput ──────────────────────────────────────────────────────────

def bench_write(db: LSMTree, n: int):
    keys   = [rk() for _ in range(n)]
    values = [rv()  for _ in range(n)]
    t0 = time.perf_counter()
    for k, v in zip(keys, values):
        db.put(k, v)
    elapsed = time.perf_counter() - t0
    print(f'  Write      {n:>7,} ops  {n/elapsed:>10,.0f} ops/sec  ({elapsed:.2f}s)')
    return keys


# ── Point read latency ────────────────────────────────────────────────────────

def bench_read(db: LSMTree, keys: list, n: int):
    sample = random.choices(keys, k=n)
    lats   = []
    for k in sample:
        t0 = time.perf_counter()
        db.get(k)
        lats.append((time.perf_counter() - t0) * 1_000)
    lats.sort()
    p50 = lats[len(lats) // 2]
    p99 = lats[int(len(lats) * 0.99)]
    print(f'  Read       {n:>7,} ops  p50={p50:.3f}ms  p99={p99:.3f}ms')


# ── Range scan throughput ─────────────────────────────────────────────────────

def bench_scan(db: LSMTree, keys: list, n: int):
    sorted_keys = sorted(keys)
    stride = max(1, len(sorted_keys) // n)
    total  = 0
    t0     = time.perf_counter()
    for i in range(0, len(sorted_keys) - stride, stride):
        total += sum(1 for _ in db.scan(sorted_keys[i], sorted_keys[i + stride]))
    elapsed = time.perf_counter() - t0
    print(f'  Scan       {n:>7,} ranges  {total:>7,} entries  ({elapsed:.2f}s)')


# ── Write amplification ───────────────────────────────────────────────────────

def bench_write_amp(db: LSMTree):
    s = db.stats()
    total_disk = sum(v['bytes'] for v in s['levels'].values())
    print(f'  Stats      memtable={s["memtable_bytes"]:,}B  '
          f'disk_total={total_disk:,}B  levels={list(s["levels"].keys())}')


# ── Write throughput: sync vs async ──────────────────────────────────────────

def bench_sync_vs_async(n: int = 5_000):
    results = {}
    for label, sync in [('sync (fsync/write)', True), ('async (no fsync)', False)]:
        if os.path.exists(DB_DIR):
            shutil.rmtree(DB_DIR)
        db = LSMTree(DB_DIR, sync_writes=sync)
        keys   = [rk() for _ in range(n)]
        values = [rv(64) for _ in range(n)]
        t0 = time.perf_counter()
        for k, v in zip(keys, values):
            db.put(k, v)
        elapsed = time.perf_counter() - t0
        db.close()
        results[label] = n / elapsed
        print(f'  {label:<30}  {n/elapsed:>10,.0f} ops/sec  ({elapsed:.2f}s)')
    overhead_x = results['async (no fsync)'] / results['sync (fsync/write)']
    print(f'  Async is {overhead_x:.1f}× faster — fsync cost per write on this disk')
    shutil.rmtree(DB_DIR, ignore_errors=True)


# ── Bloom filter FPR vs bits/key ─────────────────────────────────────────────

def bench_bloom_fpr_curve(n: int = 50_000):
    print(f'  {"bits/key":>10}  {"hash_fns":>10}  {"actual FPR":>12}  {"target FPR":>12}')
    for target in [0.10, 0.05, 0.01, 0.005, 0.001]:
        bf = BloomFilter(n, target)
        keys = [rk(20).encode() for _ in range(n)]
        for k in keys:
            bf.add(k)
        probes = [rk(21).encode() for _ in range(5_000)]
        fp     = sum(1 for k in probes if bf.may_contain(k))
        actual = fp / len(probes)
        print(f'  {bf.bit_count/n:>10.1f}  {bf.hash_count:>10}  '
              f'{actual:>12.4%}  {target:>12.4%}')


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print('=' * 60)
    print('LSMKV Benchmarks')
    print('=' * 60)

    print('\n[1] Bloom Filter False-Positive Rate (single point)')
    bench_bloom_fpr()

    print('\n[2] Bloom Filter FPR vs bits/key curve')
    bench_bloom_fpr_curve()

    print('\n[3] Write Sync vs Async  (isolates fsync cost)')
    bench_sync_vs_async(n=5_000)

    print(f'\n[4] Write / Read / Scan — async mode  (n_write={N_WRITE:,})')
    if os.path.exists(DB_DIR):
        shutil.rmtree(DB_DIR)
    db = LSMTree(DB_DIR, sync_writes=False)
    try:
        keys = bench_write(db, N_WRITE)
        time.sleep(1)
        bench_read(db, keys, N_READ)
        bench_scan(db, keys, N_SCAN)
        bench_write_amp(db)
    finally:
        db.close()
    shutil.rmtree(DB_DIR, ignore_errors=True)

    print('\nDone.')
