# Dossier alignment audit

Date: 30 October 2026. Specification: `research/LSMKV_v1_Research_Dossier.md`, especially
Parts V–VII and XIV. This audit compares local implementation requirements;
it does not independently verify the literature survey or novelty claims.

## Finding before changes

The project was based on the same LSM architecture, but had substantially
expanded beyond Version 1. Its main engine had 2,588 lines, group commit,
immutable background-flushed memtables, caches, parallel partial compaction,
TTL, transactions, batches, and numerous higher-level data structures. Default
SSTables used prefix compression. A C++ v2 also existed. These exceeded the
dossier, which explicitly lists several such features as future work.

## Changes

| Dossier requirement | Active implementation after alignment |
|---|---|
| Seven Python engine modules | Skip list, Bloom filter, WAL, SSTable, manifest, compaction, LSMTree; exports and fsync helpers |
| Skip list: level 16, probability 0.5 | Existing implementation preserved |
| Bloom filter: SHA-256 double hash; 20-byte header | Existing implementation preserved, including explicit bit_count |
| WAL: 17-byte header, payload, CRC32 | Preserved format; synchronous per-record fsync restored |
| Optional asynchronous benchmarking | `sync_writes=False` retained |
| SSTable: raw 9-byte entry headers | Prefix and zlib compression removed from active implementation |
| 4 KB blocks, CRC index and Bloom, 32-byte SSTABLE1 footer | Implemented; oversized individual entries occupy a block of their own |
| One 4 MB memtable with synchronous flush | Restored; WAL truncation follows durable SSTable and manifest publication |
| Seven levels, four-file L0 trigger | Restored |
| L1 budget 10 MB; 10× per subsequent level | Corrected former off-by-one exponent (100 MB L1 budget) |
| Merge all source and destination files | Restored; newest source records win equal-key ties |
| Keep tombstones until deepest level | Preserved |
| One background thread polling every two seconds | Restored |
| Atomic JSON manifest | Only `levels` persisted; tmp → fsync → rename → directory fsync |
| Cleanup after interrupted flush/compaction | Unreferenced .sst, .sst.tmp, and stale MANIFEST.json.tmp cleaned on open |
| Public put/get/delete/scan/close and diagnostic stats | Implemented; demo rewritten for this API |
| Original seven test modules / 62 tests | Preserved without editing their test cases |
| SIGKILL harness | Fixed invalid stdout polling, isolated temporary paths, detects early exit/timeouts and verifies nonempty acknowledged writes |
| Dossier benchmark workloads | Bloom FPR, sync/async comparison, write/read/scan retained; group-commit benchmark removed |

Beyond-v1 modules, tests, C++ v2, scaling notes, changelog, and original tools
are preserved in the complete `backups/pre_dossier_alignment.zip` snapshot.
The archive was CRC-checked using `ZipFile.testzip()`.

## Correctness details

Reads hold the manifest lock while opening/using point readers. Scans copy the
memtable and open their own readers while holding that lock, then merge outside
the lock. This prevents background compaction from deleting a file before a
reader opens it. It refines the dossier's illustrative snapshot pseudocode.

RAM remains visible during a flush until manifest publication and WAL truncation
succeed. Failed manifest publication leaves the WAL and memtable intact. Manifest
updates build a new state and publish it only after saving. A missing referenced
SSTable raises an error instead of silently dropping its keys. Recovery discards
a damaged WAL suffix after replaying its valid prefix, so new records cannot
become hidden behind old crash debris on the next restart. Background errors
are surfaced to subsequent operations instead of silently ignored.

SSTable filenames use unique identifiers instead of the dossier's illustrative
microsecond timestamps to avoid accidental filename reuse. These do not affect
the file format or publication protocol.

## Validation

- `python3 -m pytest tests -q`: **72 passed** (62 original tests plus 10 regression cases), 100.94 seconds.
- `python3 demo.py`: completed; puts, overwrite, deletion, range scan, and reopen verified.
- `python3 -m compileall -q lsm_engine demo.py benchmarks.py crash_harness.py`: passed.
- Full `python3 benchmarks.py`: completed. Bloom FPR curve: 10.02%, 5.06%, 1.22%, 0.38%, 0.08% for targets 10%, 5%, 1%, 0.5%, 0.1%. Read p50/p99: 0.250/0.338 ms; 49,900 scan entries in 0.15 s. The initial throughput run overlapped the durable tests and crash harness, so its results (94 sync and 933 async ops/sec) are affected by competing I/O and are not a controlled reproduction of the historical measurements.
- Isolated repeat after tests and crash runs completed: **291 sync ops/sec**, **229,917 async ops/sec**, **789.2×** difference for 5,000 writes per mode. This is close to the historical 281 / 221,993 ops/sec and 790× ratio, though single-run measurements are not statistical replication.
- Validation host: Python 3.14.4, macOS 26.5.2, arm64.
- `python3 crash_harness.py --runs 5 --keys 20000`: **5/5 passed**, 47,510 acknowledged writes recovered; zero missing, zero corrupt. Acknowledged counts per run: 6,479; 5,886; 13,992; 12,317; 8,836. These workloads test WAL recovery; they do not reach the 4 MB flush threshold. Flush/compaction publication is covered by the unit and failure-injection regression checks.

## Interpretation of “exactly”

The active feature scope, storage formats, thresholds, and lifecycle follow the
Version 1 design. This is a reconstruction from the dossier, not a byte-identical
historical source checkout: the directory has no Git history. Small correctness
refinements above are deliberate and separately tested.

The dossier's 62-test count, 5/5 crash runs, 25,973 writes, and 790× throughput
ratio are historical measurements. Fresh measurements must be reported with
current conditions. Finite tests provide evidence, not a proof of correctness
at every possible crash point. SIGKILL tests process crashes, not power loss.
The dossier itself identifies end-to-end compaction crash injection as future
work; the current harness does not claim to provide that coverage.

The dossier and summary were retained as supplied, including historical research
claims and results. Their bibliography, novelty assertions, hypothesis decisions,
and complexity statements need a separate research review before publication.


## Presentation layout update

The implementation and its Version 1 behavior remain unchanged. Scripts and
reference material were reorganized for demonstration:

- `demo.py` → `demos/basic_operations.py`
- New `demos/storage_lifecycle.py` demonstrates real automatic flush and background compaction without changing thresholds.
- `crash_harness.py` → `tests/integration/crash_harness.py`
- `benchmarks.py` → `benchmarks/run_benchmarks.py`
- `generate_pdf.py` → `tools/generate_pdf.py`
- Dossier and summary files → `docs/research/`

Current commands and the presentation sequence are in `PRESENTATION_GUIDE.md`.
Earlier validation commands above describe the file layout at the time they ran.

Post-organization validation: all **72 tests passed in 44.36 seconds**. Both
demo modules completed, including automatic L0 → L1 → L2 compaction and reopen
verification. The relocated crash harness passed a one-run, 300-key-limit smoke
check with 266 acknowledged writes and zero missing/corrupt entries. The moved
benchmark module's Bloom/write/read/scan functions were smoke-checked, and all
Python modules compiled successfully.

## Later read-path optimization and SQLite comparison

Recorded 12 November 2026. The active read path now caches SSTable readers and
memory-maps SSTables; point reads reject keys outside a table's key range before
the Bloom-filter and sparse-index lookup. Scans skip SSTables whose key ranges
cannot overlap the requested range and use optimized merge paths for one or two
sources. These changes are reflected in the current `lsm_engine/` implementation.

Three single-run reports are saved as `benchmark_results/after_run1.json` through
`after_run3.json`. Each compares 50,000 keys with 128-byte values against SQLite
on Python 3.14.6 and macOS 27.2.1. LSMKV won durable writes, sequential and
random bulk writes, missing-key point reads, full scans, and disk usage in all
three runs. SQLite won present-key point reads and 100-row range scans in all
three. The reports record that LSMKV reads use SSTables after close/reopen;
SQLite uses WAL, with FULL synchronous mode for durable writes and OFF for bulk
writes. Answers were checked against a Python dictionary.

These are indicative results from three runs on one host, not a statistical
study or a universal performance claim. The older `before_run3.json` is not a
perfectly controlled before/after comparison: its platform string differs, and
its present and missing point-read figures are dramatically slower than the
other reports, suggesting an earlier read-path issue. The before/after files
are retained as benchmark records; use the three `after_run` reports for the
current comparison. See `docs/PRESENTATION_GUIDE.md` for a concise presentation
table and the benchmark command.
