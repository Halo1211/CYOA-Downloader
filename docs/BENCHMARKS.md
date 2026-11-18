# Benchmarks

**No Python baseline or Rust measurements have been recorded yet.** Do not quote numbers until this table is filled from actual runs.

Hardware (per the owner): i7-13620H, 32 GB RAM, RTX 4070 Laptop. OS: Windows 11 Pro 22631.

## Plan

Use the existing Python harnesses so Python and Rust are measured the same way:

| Metric | Python harness | Rust equivalent (to be built) |
|---|---|---|
| Cold/warm startup to mapped window | `tools/benchmark_startup.py` | Same method: time to the first frame |
| Idle CPU/RAM | `tools/benchmark_idle_gui.py` | Same |
| GUI event latency during download/log floods | `tools/benchmark_gui_latency.py` | Frame-time histogram |
| Queue virtualization (large queues) | `tools/benchmark_queue_virtualization.py` | 100k rows |
| Media streaming / peak RSS | `tools/benchmark_media_streaming.py` | Same fixture |
| Network admission / throughput | `tools/benchmark_network_budget.py` | Local server, same fixture |
| Cache writes, ZIP compression | `benchmark_cache_writes.py`, `benchmark_zip_compression.py` | criterion |
| Cancellation latency | — | New for both |

Record for each run: commit, build profile, dataset, concurrency, repetitions (≥5), median and p95.

## Results

| Metric | Python | Rust | Δ | Date / commit |
|---|---|---|---|---|
| (none yet) | | | | |

## Session 4 measurements (2026-11-18, i7-13620H, Windows 11, release build)

**Workload:**
- 300 PNG images (50 KB random each, 16 MB total), 30 rows.
- Served by `python -m http.server` on 127.0.0.1.
- `--both -t 4 -w 0`.
- Cold runs use an isolated empty `USERPROFILE`, so no image cache.
- Timer: wall clock from process start to exit; peak working set sampled every 20 ms.

| Run | Python | Rust |
|---|---|---|
| Cold, wall time (3 runs / 2 runs) | 91.9 s / 91.9 s / 91.9 s | 90.6 s / 90.4 s |
| Cold, CPU time | 2.0–2.5 s | 0.4–2.6 s |
| Cold, peak working set | ~60 MB | ~98 MB |
| Warm cache, `--both` | 2.3 s (one run, its own cache) | 0.85–0.88 s |
| Warm cache, embed only | n/a | 0.28 s |
| `--help` startup | 0.37 s, 38 MB | 0.15 s, 2.9 MB |
| Executable | Python + deps | CLI 10.9 MB, GUI 17.0 MB |
| GUI idle | n/a | 0 ms CPU over 5 s, 140.6 MB working set |

**Findings:**
- **Cold runs are bound by the per-domain politeness interval.** Python's `_domain_min_interval` is 0.3 s, and 300 requests × 0.3 s ≈ 90 s. Rust first ran with no interval (0.9 s cold), which was a parity bug: it hit the server harder than Python. Rust now uses the same 0.3 s (`run::DOMAIN_MIN_INTERVAL`). Rust is therefore *not* faster on cold network downloads to one host. Both are deliberately rate-limited.
- **Rust peak memory is higher** (98 MB vs 60 MB): KI-R1 holds every payload in memory.
- **Cache interop verified.** Python got 300/300 `[CACHE HIT]` from a cache written by Rust, and warm outputs were byte-identical.
- **Unexplained one-off:** one Rust warm `--both` run in a profile that had already been written by an earlier cold run took 21.4 s. It could not be reproduced: a clean isolated cold→warm cycle gave a 300-entry index and 0.85–0.88 s warm runs. Kept as an open observation.
- **Output parity:** embedded JSON was byte-identical to Python in every run (300-image and fixture site).
