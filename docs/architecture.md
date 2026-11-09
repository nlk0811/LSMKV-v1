# Architecture
## LSMKV System Design

---

## Component Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         LSMTree (API)                           │
│              put / get / delete / scan / close                  │
└───────────┬─────────────────────────────────┬───────────────────┘
            │ write path                       │ read path
            ▼                                  ▼
┌───────────────────┐              ┌─────────────────────────┐
│    WAL (wal.py)   │              │    SkipList memtable     │
│  Append-only log  │              │  (skip_list.py)          │
│  CRC-checked      │              │  O(log n) get/put/delete │
│  Crash recovery   │              └──────────┬──────────────┘
└───────────────────┘                         │ flush (4 MB)
                                              ▼
                                ┌─────────────────────────┐
                                │   SSTableWriter          │
                                │   (sstable.py)           │
                                │   4 KB data blocks       │
                                │   Sparse index           │
                                │   Bloom filter           │
                                └──────────┬──────────────┘
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Disk Layout                              │
│                                                                  │
│   L0 (newest)  ── L1 ── L2 ── L3 ── L4 ── L5 ── L6 (oldest)   │
│                                                                  │
│   Each level is 10× larger than the previous.                   │
│   L0 allows overlapping key ranges.                              │
│   L1+ have non-overlapping key ranges (leveled compaction).      │
└──────────────────────────────┬──────────────────────────────────┘
                               │ background compaction
                               ▼
                ┌──────────────────────────┐
                │   Compaction (k-way merge)│
                │   (compaction.py)         │
                │   Min-heap merge          │
                │   Tombstone propagation   │
                └──────────┬───────────────┘
                           │ commit point
                           ▼
                ┌──────────────────────────┐
                │   Manifest               │
                │   (manifest.py)          │
                │   MANIFEST.json          │
                │   Atomic write (rename)  │
                └──────────────────────────┘
```

---

## Write Path (Step by Step)

```
put('key', 'value')
  │
  ├─ 1. Acquire _write_lock
  ├─ 2. WAL.log_put(key, value)       ← fsync to disk
  ├─ 3. SkipList.put(key, value)      ← O(log n) in-memory insert
  └─ 4. if memtable.size >= 4MB:
         └─ _flush_memtable()
              ├─ a. Write L0 SSTable  ← fsync
              ├─ b. os.rename(tmp, final)
              ├─ c. manifest.add_l0() ← atomic MANIFEST write
              └─ d. wal.truncate()    ← safe now
```

---

## Read Path (Step by Step)

```
get('key')
  │
  ├─ 1. Check SkipList memtable       ← O(log n), in memory
  │      → hit: return value
  │      → miss: continue
  │
  ├─ 2. Snapshot levels under lock
  │
  └─ 3. For each level L0 → L6:
         For each SSTable (L0: newest first):
           ├─ a. Bloom filter check   ← may_contain() = O(k), k ≈ 7
           │      → definite miss: skip file
           │      → possible hit: continue
           ├─ b. Binary search sparse index   ← O(log B), B = num blocks
           └─ c. Linear scan data block       ← O(entries_in_block)
```

---

## Compaction (Background Thread)

```
Triggers:
  L0 files >= 4       → compact L0 + L1 → L1
  Level i > budget    → compact level i + i+1 → level i+1

Algorithm:
  1. Snapshot input file paths
  2. kway_merge(inputs)      ← min-heap, O(N log K)
  3. Write output SSTable    ← fsync
  4. os.rename(tmp, final)   ← atomic
  5. manifest.apply_compaction()  ← COMMIT POINT
  6. Delete input files
```

---

## On-Disk File Format

### SSTable (.sst)
```
[Data Block 0]  4096 bytes
[Data Block 1]
...
[Data Block N]
[Index Block]   sparse index: first_key → (offset, size) per block
[Bloom Filter]  serialised bit array + CRC
[Footer]        32 bytes:
                  index_offset  (8)
                  bloom_offset  (8)
                  index_size    (4)
                  bloom_size    (4)
                  magic         (8) = b'SSTABLE1'
```

### WAL Record
```
op_type   (1 byte)   1=PUT, 2=DELETE
key_len   (4 bytes)
val_len   (4 bytes)
timestamp (8 bytes)
key       (key_len bytes)
value     (val_len bytes)
CRC32     (4 bytes)
```

### MANIFEST.json
```json
{
  "levels": [
    ["data/L0_1234.sst", "data/L0_5678.sst"],
    ["data/L1_9999.sst"],
    [],
    ...
  ]
}
```

---

## Complexity Summary

| Operation | Complexity | Notes |
|---|---|---|
| put / delete | O(log n) | Skip list insert + O(1) WAL append |
| get | O(log n · L) | L = levels; Bloom reduces most to O(1) |
| scan(k results) | O(k log k) | Heap merge across active SSTables |
| flush | O(n) | Skip list is already sorted |
| compaction (K-way) | O(N log K) | N entries, K input files |
| Bloom filter probe | O(k) | k ≈ 7 hash functions |

---

## Data Structure Roles

| Data Structure | Where Used | Why |
|---|---|---|
| Skip List | Memtable | O(log n) sorted in-memory writes; natural iteration |
| Bloom Filter | Per SSTable | Skip disk reads for keys that don't exist |
| Min-Heap | Compaction merge | Efficient K-way merge in O(N log K) |
| Sparse Index | Per SSTable | Binary search to block; avoids full file scan |
| Append-Only Log | WAL | Sequential writes = maximum disk throughput |
| JSON + atomic rename | MANIFEST | Human-readable + crash-safe via POSIX rename |
