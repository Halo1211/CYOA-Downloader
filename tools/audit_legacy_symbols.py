#!/usr/bin/env python3
"""Audit top-level symbols in the compatibility surface.

This is intentionally dependency-free and safe to run before importing the app.
It parses the file with ast, groups top-level functions/classes/constants, and
prints a Markdown report that can be redirected to docs/LEGACY_SYMBOL_MAP.md.
"""
from __future__ import annotations

import ast
import collections
import pathlib
from dataclasses import dataclass
from typing import Iterable

ROOT = pathlib.Path(__file__).resolve().parents[1]
LEGACY = ROOT / "cyoa_downloader_app" / "legacy.py"
SURFACE = ROOT / "cyoa_downloader_app" / "runtime" / "surface.py"

DOMAIN_RULES = [
    ("GUI", ("CYOADownloaderGUI", "GUILogHandler", "_v24", "_v25", "_v27", "_v46_gui", "_v461_GUI", "_v462", "_v463", "_v465_apply_theme", "_v466")),
    ("download", ("run_download", "process_images", "WebsiteDownloader", "_deep_scan", "_download", "_write_youtube", "_make_ytdlp", "_finalize", "_walk_package", "verify_output", "write_package")),
    ("network", ("fetch_response", "create_retry_session", "_get_active_proxy", "_set_active_proxy", "_set_active_dns", "_get_active_dns", "_doh", "_dns", "_patched_getaddrinfo", "flaresolverr", "is_cloudflare", "_normalize_cloudflare", "_domain_", "_throttle")),
    ("project", ("try_decode", "looks_like_project", "extract_project", "parse_jsonish", "get_project_source", "auto_detect", "CYOACafe", "cyoap", "find_script", "find_candidate", "get_source", "extract_iframe", "build_default_project")),
    ("integrations", ("AI", "_ai", "_set_itch", "itch", "gallery_dl", "_gdl", "CYOA_MANAGER", "cyoa_manager", "offline_viewer", "viewer", "PluginRegistry", "register_asset_scanner", "register_engine_detector")),
    ("config/storage", ("settings", "cache", "history", "resume", "keyring", "secret")),
    ("progress/core", ("DownloadState", "DownloadTelemetry", "DownloadCancelled", "_emit_progress", "_cancel", "calculate_", "canonicalize_url", "validate_zip", "truncate_display")),
]

@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    line: int
    end_line: int
    domain: str


def classify(name: str) -> str:
    lowered = name.lower()
    for domain, prefixes in DOMAIN_RULES:
        for prefix in prefixes:
            if name.startswith(prefix) or lowered.startswith(prefix.lower()) or prefix.lower() in lowered:
                return domain
    return "uncategorized"


def iter_symbols(tree: ast.Module) -> Iterable[Symbol]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            yield Symbol(node.name, "class", node.lineno, node.end_lineno or node.lineno, classify(node.name))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield Symbol(node.name, "function", node.lineno, node.end_lineno or node.lineno, classify(node.name))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = []
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        targets.append(target.id)
            elif isinstance(node.target, ast.Name):
                targets.append(node.target.id)
            for name in targets:
                if name.startswith("__"):
                    continue
                yield Symbol(name, "constant/global", node.lineno, getattr(node, "end_lineno", node.lineno) or node.lineno, classify(name))


def main() -> int:
    target = LEGACY if LEGACY.exists() else SURFACE
    source = target.read_text(encoding="utf-8")
    tree = ast.parse(source)
    symbols = list(iter_symbols(tree))
    by_domain = collections.Counter(s.domain for s in symbols)
    by_kind = collections.Counter(s.kind for s in symbols)
    print("# Compatibility Surface Symbol Audit")
    print()
    print(f"Source: `{target.relative_to(ROOT)}`")
    print(f"Lines: {len(source.splitlines())}")
    print(f"Top-level symbols: {len(symbols)}")
    print()
    print("## Counts by kind")
    print()
    for kind, count in sorted(by_kind.items()):
        print(f"- {kind}: {count}")
    print()
    print("## Counts by likely domain")
    print()
    for domain, count in sorted(by_domain.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"- {domain}: {count}")
    print()
    print("## Symbols")
    print()
    print("| line | kind | domain | symbol |")
    print("|---:|---|---|---|")
    for s in symbols:
        print(f"| {s.line} | {s.kind} | {s.domain} | `{s.name}` |")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
