# LSMKV — Version 1

A crash-safe Python LSM-tree key-value engine, organized for code explanation,
testing, and a live demonstration. Start with the
[presentation guide](docs/PRESENTATION_GUIDE.md).

## Project layout

```text
lsm_engine/                  Main implementation: the storage design
  lsm_tree.py                Public API, flush, recovery, background compaction
  skip_list.py               Sorted in-memory memtable
  bloom_filter.py            Membership filter and serialization
  wal.py                     CRC32 write-ahead log
  sstable.py                 Disk blocks, sparse index, Bloom filter, footer
  compaction.py              Newest-first K-way merge and tombstones
  manifest.py                Atomic record of committed SSTables
  _utils.py                  File and directory durability helpers

demos/                       Short scripts to show your professor
  interactive_shell.py       Type db.put(), db.get(), and other commands live
  basic_operations.py        Put, get, overwrite, delete, scan, reopen
  storage_lifecycle.py       Automatic flush, compaction, manifest, persistence

tests/                       Automated tests, separate from implementation
  test_*.py                  62 original tests + 10 alignment regression checks
  integration/
    crash_harness.py         Real SIGKILL recovery verification

benchmarks/
  run_benchmarks.py           Bloom FPR, durability cost, write/read/scan metrics
  read_comparison.py          Sequential vs indexed lookup, with verified answers

docs/
  PRESENTATION_GUIDE.md       Suggested explanation order and demo commands
  architecture.md            Architecture and disk formats
  invariants.md              Three crash-safety invariants
  dossier_alignment.md       Dossier comparison and validation results
  literature_survey.md       Literature notes
  research_gap.md            Research context
  research/                  Original dossier and research summary (MD/PDF)

tools/
  generate_pdf.py            Optional research-summary PDF generator
backups/                     Preserved pre-alignment project archive
```

## Get the project later

This Version 1 project is saved in the private repository
[nlk0811/LSMKV-v1](https://github.com/nlk0811/LSMKV-v1).

```sh
git clone https://github.com/nlk0811/LSMKV-v1.git
cd LSMKV-v1
python3 -m pip install -r requirements.txt
python3 -m demos.interactive_shell
```

Authenticate with the GitHub account that has access before cloning.

## Setup and demo

Run these commands from this project folder:

```sh
python3 -m pip install -r requirements.txt
python3 -m demos.basic_operations
python3 -m demos.storage_lifecycle
python3 -m pytest tests -q
```

For custom inputs during your presentation, start the interactive terminal:

```sh
python3 -m demos.interactive_shell
```

`db` is already open. At the `>>>` prompt, type:

```python
db.put("name", "Alice")
db.get("name")
list(db.scan())
db.delete("name")
print(db.get("name"))
db.stats()
exit()
```

Data stays in `demo_data/interactive` and is restored on the next launch.
Use `--data-dir demo_data/session2` for a separate demonstration database.
The basic and storage lifecycle demos use temporary directories by default;
the interactive terminal keeps its database.

For a short live crash recovery check:

```sh
python3 -m tests.integration.crash_harness --runs 1 --keys 300
```

For the full research workloads:

```sh
python3 -m tests.integration.crash_harness --runs 5 --keys 20000
python3 -m benchmarks.run_benchmarks
```

Use `python3 -m demos.storage_lifecycle --data-dir demo_data` to retain the
manifest, WAL, and SSTables for inspection. The directory must be new or empty.
Without that option, demos use temporary directories and clean up afterward.

To demonstrate retrieval performance using the same data and queries:

```sh
python3 -m benchmarks.read_comparison
```

It compares sequential scans, sparse-index lookup, and Bloom-filter-plus-index
lookup for present and missing keys separately. Results include latency,
throughput, and the throughput ratio relative to sequential scan. It checks all
answers and runs three repeats. For a larger dataset and a saved report:

```sh
python3 -m benchmarks.read_comparison --keys 50000 --queries 100 --repeats 3 --output benchmark_results/read_comparison.json
```

This is a single-SSTable component benchmark with warmed data and an open
reader; full `db.get()` has additional costs. See the presentation guide for
how to describe the comparison accurately.

The engine retains the dossier's 4 MB synchronous flush, per-record WAL fsync,
seven levels, four-file L0 compaction trigger, 10 MB L1 budget and 10× growth.
Tombstones are preserved until L6. `sync_writes=False` is for benchmarks.

## Reference material

The [Version 1 dossier](docs/research/LSMKV_v1_Research_Dossier.md) and
[research summary](docs/research/LSMKV_Research_Summary.pdf) are preserved as
historical documents. Their original root-relative file locations have changed;
use the project layout above to find the current scripts.

To regenerate the historical summary PDF, install the optional report dependency:

```sh
python3 -m pip install -r requirements-report.txt
python3 -m tools.generate_pdf
```

The PDF is written to `docs/research/LSMKV_Research_Summary.pdf`.

The prior expanded engine, tests, C++ v2, and development notes are preserved in
`backups/pre_dossier_alignment.zip`. Extract into a separate folder to inspect
that version. Its TTL/compressed databases are not supported by this Version 1
engine; use a fresh database directory for the demos.
