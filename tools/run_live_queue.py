"""Run a real CSV queue with isolated processes, checkpoints and a local catalogue.

Each URL owns its output directory, so parallel jobs never share process-global
download state or the output lease. Existing completed jobs are skipped on resume.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote, urlparse

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from cyoa_downloader_app.core.atomic_io import interprocess_file_lock
from cyoa_downloader_app.core.paths import _is_link_or_junction, _is_windows_reserved_basename


def human_folder_name(title: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", re.sub(r"\s+", " ", title)).strip(" .")
    name = name.encode("utf-8")[:95].decode("utf-8", "ignore").rstrip(" .") or "Untitled"
    if _is_windows_reserved_basename(name) or name.casefold() in {"_audit", "_program", "_releases"}:
        name = "CYOA " + name
    return name


def unique_folder_name(base: str, used: set[str]) -> str:
    candidate, number = base, 2
    while candidate.casefold() in used:
        suffix = f" ({number})"
        prefix = base.encode("utf-8")[:95 - len(suffix)].decode("utf-8", "ignore").rstrip(" .")
        candidate = prefix + suffix
        number += 1
    used.add(candidate.casefold())
    return candidate


def bind_job_folders(jobs: list[dict], root: Path, audit: Path) -> list[dict]:
    """Preserve checkpoint ownership and never overwrite unrelated folders."""
    used = {path.name.casefold() for path in root.iterdir()}
    claimed = set()
    for job in jobs:
        checkpoint = audit / f"{job['id']}.json"
        old = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.is_file() else {}
        folder = str(old.get("folder") or "")
        entry = (root / str(old.get("entry") or "")).resolve()
        resumable = ((old.get("status") == "downloaded" and entry.is_relative_to(root / folder) and entry.is_file())
                     or (old.get("status") == "running" and existing_worker(old, audit) is not None))
        owned = (resumable and old.get("url") == job["url"] and folder.casefold() not in {".", "..", "_audit", "_program", "_releases"}
                 and folder and "/" not in folder and "\\" not in folder and (root / folder).is_dir())
        if owned:
            if folder.casefold() in claimed or _is_link_or_junction(str(root / folder)):
                raise ValueError("Unsafe or duplicate checkpoint folder")
            job["folder"] = folder
            used.add(folder.casefold())
        else:
            job["folder"] = unique_folder_name(human_folder_name(job["title"]), used)
        claimed.add(job["folder"].casefold())
    return jobs


def queue_jobs(source: Path) -> list[dict]:
    jobs = {}
    used = set()
    with source.open(encoding="utf-8-sig", newline="") as stream:
        for row_number, row in enumerate(csv.DictReader(stream), 2):
            url = row.get("url", "").strip()
            if not url.startswith(("https://", "http://")):
                continue
            if url in jobs:
                jobs[url]["rows"].append(row_number)
                continue
            title = re.sub(r"\s+", " ", row.get("filename", "")).strip() or urlparse(url).hostname or "Untitled"
            identity = hashlib.sha256(url.encode()).hexdigest()[:10]
            jobs[url] = {
                "url": url,
                "title": title,
                "folder": unique_folder_name(human_folder_name(title), used),
                "mode": row.get("mode", "auto"),
                "rows": [row_number],
                "id": identity,
            }
    return list(jobs.values())


def write_json(path: Path, value) -> None:
    temporary = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    with interprocess_file_lock(str(path) + ".write", timeout=60):
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            # Windows readers/antivirus can briefly hold the destination
            # without FILE_SHARE_DELETE, even when all writers are serialized.
            for attempt in range(10):
                try:
                    temporary.replace(path)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(min(0.02 * (attempt + 1), 0.1))
        finally:
            temporary.unlink(missing_ok=True)


def existing_worker(job: dict, audit: Path):
    """Adopt a surviving worker after its supervisor was interrupted."""
    if job.get("status") != "running" or not job.get("pid"):
        return None
    try:
        import psutil
    except ImportError:
        return None
    try:
        process = psutil.Process(int(job["pid"]))
        args = process.cmdline()
        expected = os.path.normcase(str(audit / f"{job['id']}.job.json"))
        if (
            len(args) >= 5
            and args[-2] == "--worker"
            and os.path.normcase(args[-1]) == expected
            and os.path.normcase(args[0]) == os.path.normcase(sys.executable)
            and abs(process.create_time() - float(job["started"])) < 30
        ):
            return process
    except (psutil.Error, KeyError, TypeError, ValueError):
        return None
    return None


def finish_job(result: dict, root: Path, audit: Path) -> dict:
    result["seconds"] = round(time.time() - result["started"], 1)
    folder = root / result["folder"]
    entries = [folder / "index.html"] if (folder / "index.html").is_file() else sorted(
        folder.glob("*/index.html"), key=lambda path: path.stat().st_mtime, reverse=True)
    if entries:
        result["entry"] = entries[0].relative_to(root).as_posix()
    elif result["status"] == "downloaded":
        result["status"] = "missing_entry"
    write_json(audit / f"{result['id']}.json", result)
    print(f"DONE {result['id']} {result['status']} {result['seconds']}s", flush=True)
    return result


def worker(spec: Path) -> None:
    job = json.loads(spec.read_text(encoding="utf-8"))
    # Override only this process; preserve the user's saved GUI settings.
    from cyoa_downloader_app.config import settings

    original_load = settings._load_settings

    def live_settings():
        result = original_load().copy()
        result.update(
            archive_strategy="auto",
            archive_settle_time_ms=1800,
            archive_runtime_max_pages=12,
            archive_max_scroll_steps=160,
            archive_max_interactions=40,
            archive_no_progress_rounds=4,
            archive_interaction_policy="safe",
            offline_viewer_json_enabled=True,
            offline_viewer_website_enabled=True,
        )
        return result

    settings._load_settings = live_settings
    from cyoa_downloader_app.cli import main

    mode = job["mode"]
    flag = "--pure-website-folder" if mode.startswith("pure_website") else "--icc-folder"
    sys.argv = [
        "cyoa_downloader.py",
        job["url"],
        "content" if job.get("staging") else "archive",
        flag,
        "--archive-strategy",
        "auto",
        "--archive-max-pages",
        "5000",
        "--archive-max-depth",
        "100",
        "--ai-mode",
        "off",
        "--threads",
        "6",
        "--wait-time",
        "15",
        "--fonts",
        "--output",
        job.get("staging", job["output"]),
    ]
    main()
    if job.get("staging"):
        publish_job_output(job, spec.parent.parent)


def publish_job_output(job: dict, root: Path) -> None:
    """Move a complete staged viewer into its title folder without overwriting."""
    from cyoa_downloader_app.core.output import output_directory_lease

    root = root.resolve()
    destination = Path(job["output"]).absolute()
    staging = Path(job["staging"]).absolute()
    source = staging / "content"
    if (destination.parent != root or destination.name != job["folder"]
            or not staging.resolve().is_relative_to(root / "_audit" / "work")
            or _is_link_or_junction(str(source)) or _is_link_or_junction(str(destination))):
        raise ValueError("Unsafe staged output path")
    if not (source / "index.html").is_file():
        raise RuntimeError("Download did not produce an entry page; staging was retained")
    with output_directory_lease(str(destination)):
        if destination.exists():
            raise FileExistsError(f"Output folder already exists: {destination.name}")
        source.rename(destination)


def run_job(job: dict, root: Path, audit: Path, timeout: int) -> dict:
    status_file = audit / f"{job['id']}.json"
    if status_file.exists():
        old = json.loads(status_file.read_text(encoding="utf-8"))
        if old.get("status") == "downloaded" and (root / old["entry"]).is_file():
            return old
        survivor = existing_worker(old, audit)
        if survivor is not None:
            print(f"ADOPT {job['id']} pid={survivor.pid}", flush=True)
            code = survivor.wait()
            old.update(status="downloaded" if code == 0 else "failed", exit_code=code, adopted=True)
            return finish_job(old, root, audit)
        if old.get("status") == "running":
            log_path = audit / f"{job['id']}.log"
            log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
            completed = any(
                marker in log_text[-6000:]
                for marker in ("ICC download complete.", "Pure website download complete.", "ICC folder kept:")
            )
            if completed and "Traceback (most recent call last)" not in log_text[-6000:]:
                old.update(status="downloaded", exit_code=0, recovered_from_log=True)
                return finish_job(old, root, audit)
    output = root / job["folder"]
    staging = audit / "work" / job["id"] / uuid.uuid4().hex
    staging.mkdir(parents=True)
    job = dict(job, output=str(output), staging=str(staging), layout_version=2)
    spec = audit / f"{job['id']}.job.json"
    write_json(spec, job)
    started = time.time()
    result = dict(job, status="running", started=started)
    write_json(status_file, result)
    print(f"START {job['id']} {job['url']}", flush=True)
    with (
        (audit / f"{job['id']}.log").open("w", encoding="utf-8") as log,
        subprocess.Popen(
            [sys.executable, "-u", str(Path(__file__).resolve()), "--worker", str(spec)],
            cwd=REPO,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        ) as process,
    ):
        result["pid"] = process.pid
        write_json(status_file, result)
        try:
            result["exit_code"] = process.wait(timeout=timeout)
            result["status"] = "downloaded" if process.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                process.kill()
            process.wait()
            result["status"] = "timeout"
    return finish_job(result, root, audit)


def catalogue(root: Path, jobs: list[dict], audit: Path) -> None:
    results = []
    for job in jobs:
        path = audit / f"{job['id']}.json"
        result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else dict(job, status="pending")
        results.append(result)
    write_json(audit / "queue_status.json", results)
    with (audit / "retry_queue.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["url", "filename", "mode"])
        writer.writeheader()
        for item in results:
            if item["status"] != "downloaded":
                writer.writerow({"url": item["url"], "filename": item["title"], "mode": item["mode"]})
    rows = []
    for item in results:
        title = html.escape(item["title"])
        if item.get("entry"):
            title = f'<a href="{quote(item["entry"], safe="/")}">{title}</a>'
        rows.append(f"<tr><td>{title}</td><td>{html.escape(item['status'])}</td></tr>")
    page = (
        '<!doctype html><meta charset="utf-8"><title>CYOA Library</title>'
        "<style>body{font:16px/1.6 system-ui;max-width:1100px;margin:32px auto;padding:16px;"
        "background:#111827;color:#e5e7eb}a{color:#93c5fd}td{padding:8px;border-bottom:1px solid #374151}</style>"
        f"<h1>CYOA Library</h1><p>{sum(r['status'] == 'downloaded' for r in results)} / {len(results)} "
        "downloaded. Serve this folder over localhost for interactive viewers.</p>"
        "<table><tr><th>Title</th><th>Download status</th></tr>" + "".join(rows) + "</table>"
    )
    (root / "index.html").write_text(page, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--queue", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
        return
    if not args.queue or not args.output:
        parser.error("--queue and --output are required")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    audit = root / "_audit"
    audit.mkdir(exist_ok=True)
    from cyoa_downloader_app.core.output import output_directory_lease

    with output_directory_lease(str(audit)):
        jobs = bind_job_folders(queue_jobs(args.queue), root, audit)
        catalogue(root, jobs, audit)
        with ThreadPoolExecutor(max_workers=max(1, min(8, args.jobs))) as pool:
            pending = [pool.submit(run_job, job, root, audit, args.timeout) for job in jobs]
            for future in as_completed(pending):
                future.result()
                catalogue(root, jobs, audit)
    print("Queue complete. Inspect _audit/queue_status.json and retry_queue.csv.", flush=True)


if __name__ == "__main__":
    main()
