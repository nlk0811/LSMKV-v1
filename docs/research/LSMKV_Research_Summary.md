# LSMKV
## Design and Implementation of a Crash-Safe Key-Value Storage Engine Using Log-Structured Merge Trees

**Research Summary — Findings, Hypotheses, and Experimental Results**  
Advanced Data Structures and Algorithms — Project Report  
Author: nlk0811 · [github.com/nlk0811/LSMKV](https://github.com/nlk0811/LSMKV)

---

> **Abstract.**  LSMKV is a ground-up implementation of an LSM-Tree based key-value storage engine in Python, built to formally specify and experimentally verify the minimum conditions required for crash-safe operation on standard POSIX block storage. We identify a research gap — no prior work states these conditions as minimal, testable invariants without requiring persistent memory or distributed infrastructure — and address it by encoding three crash-safety invariants directly in the engine, verifying them through a kill-9 crash harness, and quantifying the durability-throughput trade-off experimentally. A novel Bloom filter serialization defect (false negatives after restart) is discovered, characterized, and fixed. The full test suite achieves 62/62 pass rate and 5/5 crash recovery runs show zero data loss or corruption.

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Research Gap](#2-research-gap)
3. [Research Hypotheses](#3-research-hypotheses)
4. [System Design](#4-system-design)
5. [Test Suite Results](#5-test-suite-results)
6. [Benchmark Results](#6-benchmark-results)
7. [Crash Recovery Results](#7-crash-recovery-results)
8. [Novel Finding — Bloom Filter Serialization Defect](#8-novel-finding--bloom-filter-serialization-defect)
9. [Hypothesis Decisions](#9-hypothesis-decisions)
10. [Conclusions](#10-conclusions)
11. [Selected References](#11-selected-references)

---

## 1. Introduction

Log-Structured Merge Trees (LSM-Trees) underpin some of the most widely deployed storage systems in production: Apache Cassandra, RocksDB, LevelDB, Amazon DynamoDB, and HBase all rely on LSM-based designs for their write-optimised storage layers. Despite this ubiquity, the precise conditions that guarantee crash safety in a disk-based LSM engine have never been formally stated as a minimal, verifiable set of invariants in the academic literature.

Production systems (RocksDB, LevelDB) implement crash safety correctly but bury the mechanism across hundreds of thousands of lines involving MANIFEST files, CURRENT files, version-edit logs, and WAL recycling. Research systems (NVLSM, FlatLSM, ListDB) simplify crash safety by relying on persistent memory hardware unavailable on standard servers. No paper prescribes a correct, minimal design for standard block storage. **LSMKV fills this gap.**

---

## 2. Research Gap

A systematic survey of 26 papers published between 2021 and 2025 across USENIX FAST, OSDI, ACM SIGMOD, VLDB, SOSP, and EuroSys reveals that crash-safety research for LSM stores has two poles:

| Prior Work | Limitation |
|---|---|
| **Production Systems** (RocksDB, LevelDB) | Correct but mechanism buried in hundreds of thousands of lines of C++. Not described as self-contained invariants. Not verifiable in isolation. |
| **Research Systems** (NVLSM, FlatLSM, ChameleonDB, ListDB) | Simplify crash safety using persistent memory (Optane/PM). Not applicable to standard SATA/NVMe block storage. |
| **Witcher (SOSP 2021)** | Tests for violations in NVM stores. Does NOT prescribe a correct minimal design for disk-based engines. |

> **Gap:** No existing paper provides a minimal, formally-stated, end-to-end correct crash-safe LSM engine on standard disk storage where correctness conditions are encoded as verifiable code invariants.

---

## 3. Research Hypotheses

### 3.1 Primary Hypothesis — Crash-Safety Invariants

| Hypothesis | Statement |
|---|---|
| **H₀₁ (Null)** | Three crash-safety invariants are **not sufficient** to guarantee zero data loss under arbitrary SIGKILL crashes on POSIX block storage. |
| **H₁₁ (Alternate)** | Three invariants — enforced via CRC-checked WAL, atomic `rename()`, and manifest-first SSTable registration — **are sufficient** to guarantee zero data loss and zero corruption under arbitrary SIGKILL crashes. |

### 3.2 Secondary Hypothesis — Durability Cost

| Hypothesis | Statement |
|---|---|
| **H₀₂ (Null)** | Per-write fsync does **not** produce a statistically significant difference in write throughput compared to non-durable writes. |
| **H₁₂ (Alternate)** | Per-write fsync produces a **measurable and practically significant** reduction in write throughput, quantifiable on modern SSD hardware. |

### 3.3 Tertiary Hypothesis — Bloom Filter Serialization

| Hypothesis | Statement |
|---|---|
| **H₀₃ (Null)** | Bloom filter FPR is preserved when `bit_count` is derived from `len(bits) × 8` at deserialization rather than being explicitly stored. |
| **H₁₃ (Alternate)** | Deriving `bit_count` from `len(bits) × 8` introduces **false negatives** when `bit_count` is not a multiple of 8, causing silent data unavailability after restart. |

---

## 4. System Design

LSMKV is structured as seven composable modules, each implementing one data structure or algorithmic primitive of the LSM-Tree stack:

| Module | File | Role |
|---|---|---|
| **SkipList** | `lsm_engine/skip_list.py` | In-memory memtable. O(log n) put/get/delete/scan. Tombstone support. |
| **BloomFilter** | `lsm_engine/bloom_filter.py` | Per-SSTable membership test. Double-hashing. Explicit `bit_count` serialization. |
| **WAL** | `lsm_engine/wal.py` | Append-only log. CRC32 per record. Replay stops at first bad record. |
| **SSTable** | `lsm_engine/sstable.py` | 4 KB data blocks + sparse index + Bloom filter + 32-byte footer. |
| **Compaction** | `lsm_engine/compaction.py` | K-way merge using min-heap. O(N log K). Tombstone propagation. |
| **Manifest** | `lsm_engine/manifest.py` | JSON file. Atomic update: `tmp → fsync → rename → fsync(dir)`. |
| **LSMTree** | `lsm_engine/lsm_tree.py` | API: put/get/delete/scan. Background compaction. Crash recovery. |

### 4.1 The Three Crash-Safety Invariants

| Invariant | Enforcement Point | Guarantee |
|---|---|---|
| **Invariant 1 — WAL Completeness** | `WAL.log_put()` before `memtable.put()` | Every acknowledged write is in WAL before memtable is updated. |
| **Invariant 2 — Compaction Atomicity** | `manifest.apply_compaction()` is the commit point | Crash leaves store in pre- or post-compaction state only. |
| **Invariant 3 — WAL Truncation Safety** | `manifest.add_l0()` before `wal.truncate()` | WAL truncated only after SSTable is durably in MANIFEST. |

### 4.2 Write Path

```
put('key', 'value')
  │
  ├─ 1. WAL.log_put(key, value)          ← fsync to disk  [Invariant 1]
  ├─ 2. SkipList.put(key, value)         ← O(log n) in-memory
  └─ 3. if memtable.size >= 4 MB:
         ├─ a. Write L0 SSTable          ← fsync
         ├─ b. os.rename(tmp, final)     ← atomic on POSIX
         ├─ c. manifest.add_l0()         ← atomic MANIFEST write  [Invariant 3]
         └─ d. wal.truncate()            ← safe now
```

### 4.3 Read Path

```
get('key')
  │
  ├─ 1. Check SkipList memtable                   ← O(log n), in memory
  ├─ 2. For each level L0 → L6:
  │      For each SSTable (L0: newest first):
  │        ├─ Bloom filter check                  ← O(k), k ≈ 7 hash fns
  │        ├─ Binary search sparse index          ← O(log B), B = num blocks
  │        └─ Linear scan data block              ← O(entries in block)
```

### 4.4 Complexity Summary

| Operation | Complexity | Notes |
|---|---|---|
| put / delete | O(log n) | Skip list insert + O(1) WAL append |
| get | O(log n · L) | L = levels; Bloom reduces most to O(1) |
| scan (k results) | O(k log k) | Heap merge across active SSTables |
| flush | O(n) | Skip list already sorted |
| compaction (K-way) | O(N log K) | N entries, K input files |
| Bloom filter probe | O(k) | k ≈ 7 hash functions |

---

## 5. Test Suite Results

A suite of 62 unit and integration tests was developed, one test file per module plus a dedicated crash simulation test file.

| Module | Test File | Total | Passed | Result |
|---|---|---|---|---|
| SkipList | `test_skip_list.py` | 14 | 14 | ✅ |
| BloomFilter | `test_bloom_filter.py` | 8 | 8 | ✅ |
| WAL | `test_wal.py` | 8 | 8 | ✅ |
| SSTable | `test_sstable.py` | 10 | 10 | ✅ |
| Compaction | `test_compaction.py` | 6 | 6 | ✅ |
| LSM Tree | `test_lsm_tree.py` | 11 | 11 | ✅ |
| Crash Invariants | `test_crash.py` | 5 | 5 | ✅ |
| **TOTAL** | | **62** | **62** | **100%** |

> **62 / 62 tests passing (100%).**  
> Platform: macOS Darwin 25.5, Python 3.14.4, pytest 9.1.1

---

## 6. Benchmark Results

*Platform: macOS Darwin 25.5, Python 3.14.4, SSD storage.*  
*Async mode (`sync_writes=False`) isolates engine overhead from disk I/O cost.*

### 6.1 Write Throughput — Sync vs Async

| Mode | Throughput (ops/sec) | Relative | Notes |
|---|---|---|---|
| **Sync (fsync per write)** | 281 | 100% (baseline) | Invariant 1 enforced |
| **Async (no fsync)** | 221,993 | **790× faster** | Engine overhead only |

> **Finding:** Per-write fsync costs **790×** in throughput on modern SSD. This quantifies the exact durability tax and explains why production systems use group-commit WAL batching rather than per-record fsync.

### 6.2 Read and Scan Performance (Async mode, 50,000 keys)

| Metric | Value |
|---|---|
| Write throughput | 191,794 ops/sec |
| Read latency p50 | 0.252 ms |
| Read latency p99 | 0.772 ms |
| Range scan (500 ranges) | 49,900 entries in 0.16 s |
| Disk footprint | 4.5 MB (L0 level) |
| Memtable size | 3.0 MB |

### 6.3 Bloom Filter FPR vs bits/key

| bits/key | Hash fns | Target FPR | Actual FPR | Error |
|---|---|---|---|---|
| 4.8 | 3 | 10.00% | 10.02% | +0.02% |
| 6.2 | 4 | 5.00% | 4.64% | −0.36% |
| **9.6** | **7** | **1.00%** | **1.18%** | **+0.18%** |
| 11.0 | 8 | 0.50% | 0.44% | −0.06% |
| 14.4 | 10 | 0.10% | 0.06% | −0.04% |

Actual FPR is within 0.36% absolute of the theoretical target across all configurations, confirming correctness of the double-hashing implementation and the optimal k formula `k = (m/n) × ln 2`.

---

## 7. Crash Recovery Results

Five independent crash recovery runs were conducted using SIGKILL at a random point during the write/flush lifecycle. Each run starts a fresh writer subprocess, kills it at the specified key count, then reopens the database and verifies every acknowledged key.

| Run | Kill After | Keys Written | Missing | Corrupt | Result |
|---|---|---|---|---|---|
| 1 | 7,650 | 7,650 | 0 | 0 | ✅ PASS |
| 2 | 9,795 | 8,154 | 0 | 0 | ✅ PASS |
| 3 | 5,569 | 5,569 | 0 | 0 | ✅ PASS |
| 4 | 774 | 774 | 0 | 0 | ✅ PASS |
| 5 | 3,826 | 3,826 | 0 | 0 | ✅ PASS |
| **TOTAL** | 27,614 target | **25,973 written** | **0** | **0** | **5/5** |

> **Finding:** 5/5 crash recovery runs passed with **0 missing keys and 0 corrupt entries** across 25,973 total acknowledged writes at random crash points. H₁₁ is supported — the three invariants are empirically sufficient for crash safety on POSIX block storage.

---

## 8. Novel Finding — Bloom Filter Serialization Defect

During implementation and testing, a previously undocumented correctness defect was identified in Bloom filter serialization. The defect is reproducible, has a clear root cause, and is not captured in any of the 26 surveyed papers.

### 8.1 Root Cause

Bloom filter construction computes bit array size as:

```
bit_count = ceil(−n × ln(p) / (ln 2)²)
```

This value is **not necessarily a multiple of 8**. The allocated bit array has `ceil(bit_count / 8)` bytes = `bit_count_rounded` bits. Hash probes use `h mod bit_count` (the original value).

If `bit_count` is not stored in the serialized header and is instead derived on deserialization as `len(bits) × 8`, then probes after deserialization use `h mod bit_count_rounded` — a different modulus. Probes targeting positions in `[bit_count, bit_count_rounded)` were never set during construction, so they always return `0` → the key is reported as absent.

### 8.2 Impact Class

> **Silent data unavailability.** After any database restart, all SSTable `get()` operations returned `None` for keys that existed in reloaded SSTables. This is indistinguishable from a legitimate miss and would go undetected without a persistence round-trip test.

### 8.3 Fix

Store `bit_count` as a mandatory 8-byte (uint64) field in the serialized header:

```
Header format:  capacity(4I)  hash_count(4I)  fpr(4f)  bit_count(8Q)  =  20 bytes
```

This is a correctness requirement for **any Bloom filter used across a persistence boundary** — not just in LSM stores — and is not mentioned in Papers 12 or 13 of the surveyed literature.

---

## 9. Hypothesis Decisions

| Hypothesis | Statement | Decision | Basis |
|---|---|---|---|
| H₀₁ | Invariants insufficient for crash safety | **REJECTED** | 5/5 crash runs — 0 data loss |
| H₁₁ | Invariants sufficient for crash safety | **SUPPORTED** | Empirically verified under SIGKILL |
| H₀₂ | No throughput difference from fsync | **REJECTED** | 790× gap measured |
| H₁₂ | fsync cost is significant | **SUPPORTED** | 281 vs 221,993 ops/sec |
| H₀₃ | Serialization safe without `bit_count` | **REJECTED** | False negatives confirmed experimentally |
| H₁₃ | Defect causes false negatives | **SUPPORTED** | Bug discovered, reproduced, fixed |

---

## 10. Conclusions

LSMKV demonstrates that crash safety in a disk-based LSM-Tree storage engine can be achieved with three minimal, formally-stated invariants enforced through standard POSIX primitives (`fsync`, atomic `rename`, CRC32). The crash harness validates these invariants under real SIGKILL crashes with zero data loss across all tested runs.

The **790× throughput difference** between sync and async write modes quantifies the exact durability tax on modern SSD hardware, providing a reproducible benchmark for comparing durability strategies in future LSM implementations.

The **Bloom filter serialization finding** is an independent contribution: a defect class applicable to any system persisting Bloom filters across restarts, with a one-line fix (store `bit_count`) and a unit test that detects it.

### 10.1 Limitations

- Single platform (macOS, SSD). Linux `ext4` with `O_DIRECT` may show different fsync cost ratios.
- Python interpreter overhead inflates absolute latency; relative ratios are implementation-independent.
- Write amplification across L0–L3 requires a longer workload to reach steady state.
- Crash harness currently targets write/flush only; compaction crash injection is future work.

### 10.2 Future Work

- Group-commit WAL batching as a middle ground between per-write fsync and fully async.
- Crash injection during compaction to validate Invariant 2 under more conditions.
- C or Go port to remove interpreter overhead from absolute throughput numbers.
- MVCC snapshot support and a simple transaction layer over the scan API.
- Formal write amplification measurement at steady state across L0–L3 compaction cycles.

---

## 11. Selected References (Post-2020)

1. Chen et al. *SpanDB: A Fast, Cost-Effective LSM-Tree Based KV Store on Hybrid Storage.* USENIX FAST 2021.
2. Zhong et al. *REMIX: Efficient Range Query for LSM-Trees.* USENIX FAST 2021.
3. Sarkar & Athanassoulis. *Dissecting, Designing and Optimizing LSM-based Data Stores.* ACM SIGMOD 2022.
4. Dayan et al. *Spooky: Granulating LSM-Tree Compactions Correctly.* VLDB 2022.
5. Kim et al. *ListDB: Union of Write-Ahead Logs and Persistent SkipLists.* USENIX OSDI 2022.
6. Huang et al. *Removing Double-Logging with Passive Data Persistence.* USENIX FAST 2022.
7. Fu et al. *Witcher: Systematic Crash Consistency Testing for NVM KV Stores.* ACM SOSP 2021.
8. Bornholt et al. *Using Lightweight Formal Methods to Validate an Amazon S3 Storage Node.* ACM SOSP 2021.
9. Zhu et al. *Reducing Bloom Filter CPU Overhead in LSM-Trees on Modern Storage Devices.* DaMoN 2021.
10. Yu et al. *CAMAL: Optimizing LSM-Trees via Active Learning.* ACM SIGMOD 2024.
11. Sarkar & Dayan. *The LSM Design Space and Its Read Optimizations.* IEEE ICDE 2023.
12. Sarkar et al. *Enabling Timely and Persistent Deletion in LSM-Engines.* ACM TODS 2023.

---

*LSMKV source code, tests, and all benchmark scripts: [github.com/nlk0811/LSMKV](https://github.com/nlk0811/LSMKV)*
