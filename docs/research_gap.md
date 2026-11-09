# Research Gap
## LSMKV: Crash-Safe Key-Value Storage Engine

---

## The Gap in One Sentence

No existing paper provides a minimal, formally-stated, end-to-end correct
crash-safe LSM engine on **standard disk storage** — without relying on
persistent memory or distributed infrastructure — where the correctness
conditions are encoded as verifiable code invariants.

---

## Why the Gap Exists

The literature has two poles:

### Pole 1 — Production Systems (RocksDB, LevelDB)
Implement crash safety correctly but the mechanism is buried across hundreds of
thousands of lines of C++:
- MANIFEST file + CURRENT file rotation
- Version-edit log
- WAL recycling and checkpointing

None of this is described as a self-contained, testable invariant. A reader
cannot determine *why* the system is safe without months of code archaeology.

### Pole 2 — Research Systems (NVLSM, FlatLSM, ChameleonDB, ListDB)
Simplify crash safety by relying on:
- Persistent memory's byte-granular atomicity
- Hardware-level durability not available on a standard laptop or server SSD

These systems do not apply to conventional disk-based deployments.

### What is Missing
A clean, **pedagogically complete** formulation of exactly what is required for
disk-based LSM crash safety — stated as named invariants, enforced in code, and
verified by automated crash injection.

---

## Why Witcher (SOSP '21) Does Not Fill the Gap

Witcher (Paper 16) tests for *violations* of crash consistency in NVM-backed
stores. It does not:
- Prescribe a correct minimal design
- Target conventional block-storage LSM engines
- Provide the three invariants as a reusable specification

LSMKV addresses the prescriptive side: what must be true and why.

---

## The Three Invariants LSMKV Specifies and Enforces

See `docs/invariants.md` for full specification.

**Invariant 1 — WAL Completeness**
Every acknowledged write is recoverable after any crash.

**Invariant 2 — Compaction Atomicity**
A compaction that crashes leaves the store in either the pre- or
post-compaction state. Never in between.

**Invariant 3 — WAL Truncation Safety**
A WAL segment may only be deleted after its SSTable is durably recorded
in the MANIFEST.

---

## What Makes This a Research Contribution

| Criterion | LSMKV |
|---|---|
| Novel | No paper presents a minimal formally-stated disk-based LSM with verified crash safety |
| Implementable | Three invariants + ~300 lines of Python for WAL + manifest + fsync protocol |
| Differentiated | Witcher tests for bugs; LSMKV prescribes a correct design |
| Applicable | Targets standard block storage, no special hardware required |
| Reproducible | Crash injection harness runs on any POSIX system |
