# Literature Survey
## LSMKV: Design and Implementation of a Crash-Safe Key-Value Storage Engine Using Log-Structured Merge Trees
### 26 Papers — 2021–2025

---

## Group A — LSM-Tree Architecture and General Optimizations

| # | Title | Authors | Year | Venue | Contribution |
|---|---|---|---|---|---|
| 1 | SpanDB: A Fast, Cost-Effective LSM-Tree Based KV Store on Hybrid Storage | Chen et al. | 2021 | USENIX FAST '21 | Places WAL and top-level LSM on NVMe; bulk data on SATA SSD |
| 2 | Nova-LSM: A Distributed, Component-Based LSM-Tree Key-Value Store | Huang & Ghandeharizadeh | 2021 | ACM SIGMOD '21 | Decouples memtable/compaction/storage across servers |
| 3 | REMIX: Efficient Range Query for LSM-Trees | Zhong et al. | 2021 | USENIX FAST '21 | Virtual index eliminating full-merge cost for range queries |
| 4 | TridentKV: Read-Optimized LSM-Tree via Adaptive Indexing | Lu et al. | 2021 | IEEE TPDS | Three-level adaptive indexing (hash + learned + Bloom) |
| 5 | Leveraging NVMe SSDs for a Fast, Cost-Effective LSM-Tree KV Store | Li et al. | 2021 | ACM TOS | Extended SpanDB — tiered storage placement analysis |

---

## Group B — Compaction Strategy

| # | Title | Authors | Year | Venue | Contribution |
|---|---|---|---|---|---|
| 6 | Dissecting, Designing, and Optimizing LSM-based Data Stores | Sarkar & Athanassoulis | 2022 | ACM SIGMOD '22 | Formal taxonomy of LSM design choices and amplification trade-offs |
| 7 | Constructing and Analyzing the LSM Compaction Design Space | Sarkar et al. | 2022 | EDBT '23 | Benchmarks across a large compaction policy space |
| 8 | Spooky: Granulating LSM-Tree Compactions Correctly | Dayan et al. | 2022 | VLDB '22 | Correctness conditions for safe partial compactions |
| 9 | Improving LSM-Tree KV Stores with Fine-Grained Compaction | Sun et al. | 2023 | IEEE TCC | Fine-grained scheduler reducing write-stall latency spikes |
| 10 | CaaS-LSM: Compaction-as-a-Service for Disaggregated Storage | Yu et al. | 2024 | SIGMOD '24 | Offloads compaction to dedicated compute nodes |
| 11 | Rethinking Compaction Policies in LSM-Trees | Wang et al. | 2025 | SIGMOD '25 | Hybrid policies adapting to observed skew at runtime |

---

## Group C — Bloom Filter Optimizations

| # | Title | Authors | Year | Venue | Contribution |
|---|---|---|---|---|---|
| 12 | Reducing Bloom Filter CPU Overhead in LSM-Trees | Zhu et al. | 2021 | DaMoN '21 | Cache-friendly filter layouts to reduce probe latency on fast NVMe |
| 13 | CAMAL: Optimizing LSM-Trees via Active Learning | Yu et al. | 2024 | SIGMOD '24 | Active learning to tune bits-per-key and size-ratio jointly |

---

## Group D — Write-Ahead Log and Crash Consistency

| # | Title | Authors | Year | Venue | Contribution |
|---|---|---|---|---|---|
| 14 | ListDB: Union of WALs and Persistent SkipLists on PM | Kim et al. | 2022 | USENIX OSDI '22 | Collapses WAL + memtable into single persistent skip list |
| 15 | Removing Double-Logging with Passive Data Persistence | Huang et al. | 2022 | USENIX FAST '22 | Eliminates redundant WAL writes under relational DB layer |
| 16 | Witcher: Systematic Crash Consistency Testing for NVM KV Stores | Fu et al. | 2021 | ACM SOSP '21 | Automated crash injector for NVM-based stores |
| 17 | Using Lightweight Formal Methods to Validate an Amazon S3 Node | Bornholt et al. | 2021 | ACM SOSP '21 | TLA+ + property-based testing to validate crash-safety invariants |

---

## Group E — NVM / Persistent Memory

| # | Title | Authors | Year | Venue | Contribution |
|---|---|---|---|---|---|
| 18 | NVLSM: PM KV Store with Accumulative Compaction | Zhang & Du | 2021 | ACM TOS | All LSM levels on PM with amortized compaction |
| 19 | ChameleonDB: A Key-Value Store for Optane PM | Zhang et al. | 2021 | EuroSys '21 | Exploits PM byte-addressability for memtable and WAL |
| 20 | Revisiting LSM-Based OLTP Storage with Persistent Memory | Yan et al. | 2021 | VLDB '21 | Group-commit WAL redesign for PM bandwidth constraints |
| 21 | FlatLSM: Write-Optimized LSM-Tree for PM KV Stores | He et al. | 2023 | ACM TOS | Two-level PM structure slashing write amplification |

---

## Group F — ZNS SSD Co-Design

| # | Title | Authors | Year | Venue | Contribution |
|---|---|---|---|---|---|
| 22 | WALTZ: Zone Append to Tighten LSM Tail Latency | Lee et al. | 2023 | VLDB '23 | Pipelines WAL writes via ZNS zone-append to cut p99 latency |
| 23 | SplitZNS: Towards Efficient LSM-Tree on ZNS SSDs | Huang et al. | 2023 | ACM TACO | Level-aware zone allocation matching SSTable lifetimes |

---

## Group G — Read/Write Amplification and Deletion

| # | Title | Authors | Year | Venue | Contribution |
|---|---|---|---|---|---|
| 24 | TriangleKV: Reducing Write Stalls with NVM Buffer | Ding et al. | 2022 | IEEE TPDS | NVM-resident triangle structure absorbs burst writes |
| 25 | The LSM Design Space and Its Read Optimizations | Sarkar & Dayan | 2023 | IEEE ICDE '23 | Unified read-cost model across indexes, Bloom, and merge iterators |
| 26 | Enabling Timely and Persistent Deletion in LSM-Engines | Sarkar et al. | 2023 | ACM TODS | Timestamped delete manifests for hard deletion-visibility guarantees |

---

## Key Observations

### Heavily Studied Areas
- **Compaction strategies** (Papers 6–11) — the largest cluster; tiering vs. leveling, partial compaction, adaptive triggers
- **NVM/PM integration** (Papers 14, 18–21) — dominant theme 2021–2023
- **Write amplification** (Papers 1, 5, 9, 11, 24)
- **ZNS SSD co-design** (Papers 22–23)
- **Bloom filter tuning** (Papers 12–13)

### Recommended Reading Order for LSMKV
1. Papers 6, 7, 25 — understand the design-space vocabulary
2. Papers 14, 15, 16, 17 — WAL and crash consistency
3. Papers 8, 9, 11 — compaction correctness
4. Papers 12, 13 — Bloom filter implementation
