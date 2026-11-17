---
name: gui-performance
description: Design and test responsive Rust desktop UI and asynchronous downloader engine.
---
# UI performance
Separate UI state/rendering, download scheduler, network, parsing and storage. No blocking I/O on UI thread. Use bounded concurrency, cancellation, throttled progress, timeouts, retry/backoff and graceful shutdown. Offload CPU-heavy work appropriately. Measure startup, CPU/RAM, throughput, UI responsiveness and cancellation using repeatable workloads; compare against Python baseline. No unsupported performance claims. Respect website access policies and user permissions.
