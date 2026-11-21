"""Rebuild catalogue snapshots from job checkpoints while a queue is running."""

from __future__ import annotations

import argparse
import ctypes
import json
import time
from collections import Counter
from ctypes import wintypes
from pathlib import Path

from run_live_queue import catalogue, queue_jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--supervisor-pid", required=True, type=int)
    args = parser.parse_args()
    root = args.output.resolve()
    audit = root / "_audit"
    jobs = queue_jobs(args.queue)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, args.supervisor_pid)
    previous = None
    try:
        while True:
            catalogue(root, jobs, audit)
            values = json.loads((audit / "queue_status.json").read_text(encoding="utf-8"))
            counts = dict(Counter(item["status"] for item in values))
            if counts != previous:
                print(json.dumps(counts), flush=True)
                previous = counts
            if not handle or kernel.WaitForSingleObject(handle, 0) != 258:
                break
            time.sleep(10)
    finally:
        if handle:
            kernel.CloseHandle(handle)


if __name__ == "__main__":
    main()
