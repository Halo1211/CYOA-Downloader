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
