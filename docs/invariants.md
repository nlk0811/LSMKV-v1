# Crash-Safety Invariants
## LSMKV Formal Specification

---

## Overview

LSMKV enforces three crash-safety invariants that together guarantee the
database is always in a consistent, recoverable state — regardless of when
the process is killed.

A crash can happen at any point between two machine instructions.
The invariants bound the damage to at most the in-flight write.

---

## Invariant 1 — WAL Completeness

### Statement
> For every Put or Delete operation that returns successfully to the caller,
> a durable, CRC-verified record exists in the WAL or in an SSTable before
> the return.

### Enforcement in Code
```
WAL.log_put(key, value)      ← durable write to WAL
self._memtable.put(key, value)  ← then memtable update
```
See `lsm_engine/lsm_tree.py → LSMTree.put()`.

### Recovery Procedure
On startup, `LSMTree._recover()` replays every valid WAL record into the
memtable. Records are accepted until the first CRC mismatch or truncated
record (the crash point). Everything before the crash point is restored.

### Consequence of Violation
If the memtable were updated before the WAL write, a crash between the two
would silently lose an acknowledged write. The caller would have received a
success response for a key that is now unrecoverable.

---

## Invariant 2 — Compaction Atomicity

### Statement
> After a crash during compaction, the database is in one of exactly two
> states: (a) the pre-compaction state, or (b) the post-compaction state.
> It is never in a partial state where some input files are gone but the
> output file is not yet registered, or vice versa.

### Enforcement in Code
The MANIFEST update is the **commit point**:

```
Step 1: compact(input_files, tmp_path)     ← write + fsync new SSTable
Step 2: os.rename(tmp_path, final_path)    ← atomic on POSIX
Step 3: fsync(directory)                   ← make rename durable
Step 4: manifest.apply_compaction(...)     ← COMMIT POINT (tmp → rename → fsync)
Step 5: os.remove(old_files)               ← cleanup, safe to fail
```

See `lsm_engine/lsm_tree.py → LSMTree._compact_into()`.

### Crash Scenarios

| Crash Point | State on Restart | Action |
|---|---|---|
| During Step 1 | tmp file exists, not in MANIFEST | `_cleanup_orphans()` removes tmp file |
| After Step 2, before Step 4 | final .sst exists, not in MANIFEST | `_cleanup_orphans()` removes it |
| After Step 4, before Step 5 | MANIFEST has new file; old files still on disk but not referenced | `_cleanup_orphans()` removes old files |
| After Step 5 | Clean post-compaction state | Nothing to do |

### Why os.rename() Is Sufficient
On POSIX systems `os.rename(src, dst)` is guaranteed to be atomic with
respect to a crash. Either the old name or the new name exists — never
neither, never both. This is the fundamental primitive for atomic file
operations on POSIX.

---

## Invariant 3 — WAL Truncation Safety

### Statement
> The WAL segment covering a flushed memtable may only be deleted (truncated)
> after the corresponding SSTable has been durably written AND its path has
> been atomically recorded in the MANIFEST.

### Enforcement in Code
```
Step 1: write SSTable to tmp path
Step 2: SSTableWriter.finish()          ← fsync inside
Step 3: os.rename(tmp, final_path)      ← atomic
Step 4: fsync(directory)
Step 5: manifest.add_l0(final_path)     ← MANIFEST update (atomic write + fsync)
Step 6: wal.truncate()                  ← only NOW safe
```

See `lsm_engine/lsm_tree.py → LSMTree._flush_memtable()`.

### Why Ordering Matters

If the WAL were truncated **before** the MANIFEST update (between steps 5 and 6):

- A crash between WAL truncation and MANIFEST update would leave the DB
  in a state where:
  - The WAL has been erased (no recovery source)
  - The SSTable is not in the MANIFEST (not findable)
  - The data from that memtable is permanently lost

By enforcing Step 5 before Step 6, we guarantee that even if we crash
immediately after truncating the WAL, the data is safely in an SSTable
that the MANIFEST knows about.

### Crash-Between-Flush-and-Truncate
If the crash happens after Step 5 but before Step 6 (WAL truncation):

- On restart, WAL replay re-creates the memtable entries.
- Those entries are ALSO in the L0 SSTable.
- This creates duplicates, but the read path handles them correctly:
  the memtable takes precedence, and on the next flush/compaction the
  duplicate is merged away.
- No data loss, no corruption.

---

## Verification

The invariants are tested in:
- `tests/test_crash.py` — unit-level crash simulations
- `crash_harness.py` — process-kill integration test (kill -9 during write/flush)

```
python crash_harness.py --runs 10 --keys 30000
```
