# GUI queue guide

## Change a mode without removing the URL

1. Add the URL to the queue normally.
2. Select its mode badge, such as `auto`.
3. Choose the desired output mode.

The selected mode is stored on that job immediately. Its URL, queue order, and
filename remain unchanged. Selecting an explicit mode clears the previous
auto-detected result so the row is no longer presented as automatically
classified.

## Export and import the queue

1. Select **Export List…** in the Input area.
2. Choose `.csv` for spreadsheet-compatible data or `.txt` for a simple text
   representation.
3. Save the file and use **Import List…** to load it again later.

CSV columns:

```text
url,filename,mode
https://example.com/cyoa/,Example,website_folder
```

TXT representation:

```text
https://example.com/cyoa/ | Example | website_folder
```

The `auto` mode is preserved across export and import. Internal fields such as
the job identifier are intentionally not exported.

Local TXT and CSV imports recognize UTF-8, UTF-16, and UTF-32 Unicode BOMs.
Use CSV when a queue field contains a pipe (`|`) or a line break. TXT cannot
represent those values; export reports an error and preserves the previous file.

## Measuring GUI responsiveness

Run the offline Tk benchmark with an isolated settings profile:

```powershell
python tools/benchmark_gui_latency.py --output gui-latency.json --assert-max-ms 100 --assert-log-p95-ms 250 --assert-progress-all
```

The benchmark uses a mapped, transparent window and exercises the real log,
status, and progress handlers at idle, 200 requested records/second, and a burst
of 5,000 requested records/second. It reports event-loop scheduling delay, the
time from producing a log record until inserting it in the GUI, CPU time, peak
process memory, and how many progress records were applied. It does not measure
physical mouse/compositor latency or perform network downloads. Timings vary
with machine load. `--log-control` and `--progress-control` accept trusted source
snapshots for controlled before/after comparisons.

Log rendering keeps timestamp, severity, and semantic colors in native Text
inserts, with small batches and an 8 ms work budget. Pending records stay queued
when a tick reaches its budget. Progress processing uses the existing bounded
queue, drains within its time budget, and reserves time for important events.
Widget redraws are coalesced to 150 ms; lifecycle changes refresh immediately,
and the last pending frame is retained when work falls quiet. Polling remains
infrequent when the application is idle.

Use `--queue-size 138 --assert-queue-font-growth 10` to include a realistic list
import in the benchmark. Parsing runs on a worker, and row widgets are committed
in short Tk turns. Add/import/export/download controls wait until the import is
complete; Clear cancels pending rows. Row order, filenames, modes, and queue IDs
remain intact. Queue rows share five named fonts instead of allocating a new
font for every label/button. Slicing can increase total import duration while
keeping the window responsive throughout.
