"""Collect HTTP and control metadata for a live queue without interpreting page text."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from run_live_queue import queue_jobs


def profile(job):
    result = {"id": job["id"], "url": job["url"]}
    try:
        response = requests.get(job["url"], timeout=25)
        soup = BeautifulSoup(response.content, "html.parser")
        result.update(
            status=response.status_code,
            final_url=response.url,
            bytes=len(response.content),
            controls=[
                {
                    "tag": t.name,
                    "text": t.get_text(" ", strip=True)[:130],
                    "id": t.get("id", ""),
                    "role": t.get("role", ""),
                }
                for t in soup.select('button,[role="button"],summary,input[type="button"]')
            ][:60],
            iframes=[t.get("src", "") for t in soup.select("iframe")],
            scripts=[t.get("src", "inline") for t in soup.select("script")][:30],
        )
    except requests.RequestException as exc:
        result["error"] = str(exc)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("queue", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    results = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(profile, job) for job in queue_jobs(args.queue)]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Profiled {len(results)} URLs; metadata saved to {args.output}")


if __name__ == "__main__":
    main()
