# Professor presentation guide

Open the project folder in your editor and run commands from its root. The
implementation is in `lsm_engine/`; demonstration scripts are in `demos/`;
automated verification is in `tests/`. No engine changes are needed for a demo.

## Suggested presentation: 10–15 minutes

### 1. State the problem — one minute

“LSMKV is a disk-based key-value storage engine built using an LSM tree. It
buffers sorted writes in memory, writes immutable sorted tables to disk, and
merges those tables in the background. The design focuses on preserving
acknowledged writes after a process crash.”

Use `docs/architecture.md` to show the architecture. The write path is:

```text
put/delete -> WAL + fsync -> skip-list memtable
                               |
                         4 MB threshold
                               |
                         SSTable + fsync
                               |
                       atomic MANIFEST update
                               |
                          WAL truncation
```

### 2. Explain the implementation — four minutes

Read these files in this order. Focus on the listed methods rather than reading
every line.

| File | Methods or structures to show | Explanation |
|---|---|---|
| `lsm_engine/lsm_tree.py` | `put`, `get`, `delete`, `scan` | The API and how components connect |
| `lsm_engine/skip_list.py` | `_Node`, `put`, `get`, `scan` | A sorted memtable with expected O(log n) operations |
| `lsm_engine/wal.py` | `_append`, `replay` | Log before RAM; CRC32 rejects an incomplete or damaged suffix |
| `lsm_engine/bloom_filter.py` | `_hashes`, `serialize`, `deserialize` | Skip definite misses; preserve bit_count across persistence |
| `lsm_engine/sstable.py` | `SSTableWriter.finish`, `SSTableReader.get` | 4 KB target blocks, sparse index, Bloom filter, 32-byte footer |
| `lsm_engine/manifest.py` | `_commit` | tmp file → fsync → rename → directory fsync |
| `lsm_engine/compaction.py` | `kway_merge`, `compact` | Heap merge; newer values win; tombstones shadow older records |
| `lsm_engine/lsm_tree.py` | `_flush_memtable`, `_maybe_compact`, `_compact_into`, `_recover` | Complete the storage lifecycle |

`_utils.py` supplies durable file and directory synchronization. Package exports
are in `__init__.py`. The engine imports no test or demo code.

### 3. Run the basic API demo — one minute

```sh
python3 -m demos.basic_operations
```

Explain each printed result: insert, update, deletion, sorted half-open range
scan, and persistence after close/reopen. `[key03, key07)` includes key03 through
key06. Deleted keys return `None` and are excluded from scans.

This demo is short and cleans up its own temporary database.

### 4. Show the real storage lifecycle — two minutes

```sh
python3 -m demos.storage_lifecycle
```

The script writes about 16 MB using the actual 4 MB threshold and default
per-record fsync. It prints progress after each automatic flush, waits for the
normal background worker to merge L0 into L1 and then L2 (16 MB exceeds the
10 MB L1 budget), displays the manifest's filenames,
then verifies an update and a tombstone after reopening. It uses the public API;
it does not lower engine thresholds or manually force compaction.

To show the physical files afterward, use a fresh folder:

```sh
python3 -m demos.storage_lifecycle --data-dir demo_data
```

Open `demo_data/MANIFEST.json` in the editor. Its seven lists represent L0–L6.
The `.sst` files are binary; show their layout in `docs/architecture.md`.
The WAL can be empty after close because every covered record is now in an
SSTable registered in the manifest. Use a different empty directory on a rerun.

### 5. Explain correctness and run verification — two minutes

Show `docs/invariants.md` and connect each invariant to code:

| Invariant | Code to show | What it prevents |
|---|---|---|
| WAL Completeness | `WAL._append`, `LSMTree.put/delete` | Acknowledging a write before durable storage |
| Compaction Atomicity | `Manifest._commit`, `LSMTree._compact_into` | Publishing a partial replacement for input tables |
| WAL Truncation Safety | `LSMTree._flush_memtable` | Discarding the WAL before its SSTable and manifest are durable |

Run the focused regression checks for a quick explanation:

```sh
python3 -m pytest tests/test_dossier_alignment.py -q
```

They verify per-record fsync, the disk entry format, the 10 MB L1 budget,
newest-value ordering, failure before manifest publication, publication before
WAL truncation, missing-file detection, and continued writes after WAL repair.

Run all tests when time permits:

```sh
python3 -m pytest tests -q
```

Expected: **72 passing tests**, comprising the original 62 and 10 regression
cases. Original tests are grouped by component in `tests/test_skip_list.py`,
`test_bloom_filter.py`, `test_wal.py`, `test_sstable.py`, `test_compaction.py`,
`test_lsm_tree.py`, and `test_crash.py`. Full-suite duration depends on disk sync
latency; prepare a fresh result before the presentation.

### 6. Demonstrate a real process crash — one minute

```sh
python3 -m tests.integration.crash_harness --runs 1 --keys 300
```

The script starts a separate writer, records acknowledged writes, sends SIGKILL,
opens the database again, and compares all logged keys with their expected
values. Expected output includes `pass: True`, zero missing, zero corrupt, and
`Result: 1/1 runs passed`. The exact acknowledged count varies with kill timing.
Only the writer subprocess is killed. Demo files are isolated and cleaned up.

For the full five-run research workload, use:

```sh
python3 -m tests.integration.crash_harness --runs 5 --keys 20000
```

These small records remain below the 4 MB memtable threshold. This harness
checks WAL recovery; flush and compaction publication ordering have separate
unit and failure-injection checks. SIGKILL tests process crashes, not power loss.

## Live custom inputs from your professor

Run from the project root:

```sh
python3 -m demos.interactive_shell
```

The terminal starts with `db` already created. Enter commands at its `>>>` prompt:

```python
db.put("student:101", "Alice")
db.get("student:101")
db.put("student:101", "Bob")
list(db.scan("student:", "student;"))
db.delete("student:101")
print(db.get("student:101"))
db.stats()
exit()
```

Substitute the keys and values the professor gives you. Strings require quotes;
`db.put()` takes two arguments and `db.get()` takes one. Use `list(db.scan())`
to display all live records. Use `print(db.get(...))` to explicitly display
`None` for a missing/deleted key, since Python's interactive prompt suppresses
bare `None` results. Exceptions are shown and the terminal stays open for the
next command. Tab completion is available when the host supports readline.

Exit with `exit()` or Ctrl-D. The engine closes and flushes automatically.
Launch the same command again to show persistence: the database is kept in
`demo_data/interactive`. For a separate session, use
`python3 -m demos.interactive_shell --data-dir demo_data/session2`.

## Optional: discuss performance

```sh
python3 -m benchmarks.run_benchmarks
```

Run this separately from tests and the crash harness so they do not compete for
disk I/O. It measures Bloom false positives, sync versus async write throughput,
read latency, and range scans. The full script takes longer than the short demos;
prepare its output before the presentation.

The alignment validation measured 291 sync versus 229,917 async writes/second,
a 789.2× ratio on that host. The dossier reports historical measurements of
281 and 221,993, or about 790×. Explain that fsync dominates durable write cost
on that machine; these numbers are measurements, not portable guarantees.

### SQLite comparison

To compare the current engine with SQLite on the same workloads, run:

```sh
for i in 1 2 3; do
  python3 -m benchmarks.sqlite_comparison --keys 50000 \
    --output benchmark_results/after_run$i.json
done
```

Each report is one run on the same machine and workload; the three runs show
consistent winners, but are indicative measurements rather than a statistical
study. In these November 2026 runs (Python 3.14.6, macOS 27.2.1, 50,000 keys,
128-byte values), LSMKV was faster for durable writes, sequential and random
bulk writes, missing-key point reads, and full scans. LSMKV also used less disk
space. SQLite remained faster for present-key point reads and 100-row range
scans:

| Workload | LSMKV result | SQLite result | Winner |
|---|---:|---:|---|
| Durable writes | 265–276 ops/s | 254–272 ops/s | LSMKV in all 3 runs |
| Bulk writes, sequential | 198k–235k ops/s | 159k–169k ops/s | LSMKV in all 3 runs |
| Bulk writes, random | 200k–213k ops/s | 109k–118k ops/s | LSMKV in all 3 runs |
| Point read, present key (p50) | 3.6–3.7 µs | 2.2–2.3 µs | SQLite in all 3 runs |
| Point read, missing key (p50) | 0.58–0.62 µs | 1.37–1.70 µs | LSMKV in all 3 runs |
| Range scan, 100 rows (p50) | 37.5–38.0 µs | 31.1–32.0 µs | SQLite in all 3 runs |
| Full scan | 3.23m–3.36m rows/s | 2.41m–2.69m rows/s | LSMKV in all 3 runs |
| Disk usage | 7.51 MB | 8.68 MB | LSMKV in all 3 runs |

The benchmark validates answers against a Python dictionary. Reads are issued
after close and reopen, so the LSMKV read results exercise SSTables. SQLite is
configured with WAL mode; durable writes use FULL synchronous mode and bulk
writes use synchronous OFF. State these conditions when presenting the numbers.
This is a workload-specific comparison, not evidence that LSMKV is faster for
all databases or query patterns.

## Demonstrating faster retrieval

Run this command in the terminal during the presentation:

```sh
python3 -m benchmarks.read_comparison
```

It builds one SSTable containing 20,000 records with 128-byte values, then runs
the same 100 present-key and 100 missing-key queries with three strategies:

1. **Sequential scan:** scan sorted records from the beginning, stopping at the
   target or the first larger key. This is the explicit baseline.
2. **Sparse index only:** binary-search the block index and search that block.
3. **Bloom + sparse index:** the actual `SSTableReader.get()` path, which rejects
   definite misses before searching the index and block.

All answers are verified, every strategy runs three times, and the execution
order rotates between repeats. The file's blocks are warmed first, and one
reader is kept open. There is no active memtable, multi-level lookup, or
per-query reader-open cost in these measurements.

The output distinguishes:

- **p50 latency:** the median query duration in microseconds; lower is better.
- **p95 latency:** a duration at or below which about 95% of measured queries fall.
- **Lookups/second:** queries divided by the median total lookup time over repeats;
  higher is better.
- **vs scan:** the throughput ratio relative to sequential scan for that category.
  It is not calculated by dividing the p50 values.

A run on the validation host produced:

| Query category | Sequential p50 | Bloom + index p50 | Throughput vs sequential |
|---|---:|---:|---:|
| Present keys | 1,605.94 µs | 8.79 µs | 193.9× |
| Missing keys | 1,946.29 µs | 0.83 µs | 1,490.7× |

The sparse index alone achieved 7.29 µs p50 for hits and 9.33 µs for misses.
This shows that Bloom hashing adds work for hits while accelerating definite
misses. Avoid claiming that Bloom filters make every query faster.

This is a different comparison from `sqlite_comparison`: it measures an SSTable
lookup against a sequential scan of that same SSTable, not against SQLite or
end-to-end database reads.

Say: “For this 20,000-record workload, the SSTable's Bloom-filter-and-index
lookup achieved about 194 times the present-key throughput of a sequential
scan. The index narrows the search to one block, and the Bloom filter can reject
missing keys before reading a data block.”

Then state: “This comparison measures warmed lookups in one open SSTable.
End-to-end `db.get()` includes other costs, and results vary with the workload
and machine.” A claim of improvement over an older engine version requires
running both versions on the same dataset and query workload.

The measured result is saved in `benchmark_results/read_comparison.json`.
Regenerate it with:

```sh
python3 -m benchmarks.read_comparison --output benchmark_results/read_comparison.json
```

For scaling experiments, change `--keys` while keeping the other settings the
same. For example, repeat with 10,000, 20,000, and 50,000 records. Keep other
benchmarks and tests stopped while collecting results.

## Questions to prepare for

- **Why a skip list?** It keeps the memtable sorted with expected logarithmic
  insertion and lookup, which makes ordered flushing and range scans natural.
- **Why both a WAL and SSTables?** The WAL protects writes still buffered in RAM;
  SSTables store sorted data for longer-term reads and compaction.
- **Can a Bloom filter say a missing key exists?** Yes, a false positive causes
  an extra lookup. Correctly serialized filters must not reject inserted keys.
- **Why store bit_count explicitly?** Byte padding can change the modulus if it
  is reconstructed from byte length, changing probe positions after reopening.
- **Why tombstones?** Removing a RAM entry would expose an older disk value.
  A tombstone hides that value until it is safe to discard during L6 compaction.
- **What proves crash safety?** Explain the publication protocol and the evidence
  from tests. Finite crash runs support correctness; they are not a formal proof
  over every possible execution or power-loss scenario.
- **What is future work?** Group commit, systematic compaction crash injection,
  longer steady-state benchmarks, MVCC, and formal modeling. These are outside
  the active Version 1 implementation.

The original dossier and summary are in `docs/research/`. The implementation
comparison and recorded validation are in `docs/dossier_alignment.md`.
