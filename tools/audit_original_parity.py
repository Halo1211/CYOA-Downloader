#!/usr/bin/env python3
"""Compare the refactored facade against an original single-file script.

Usage:
    python tools/audit_original_parity.py /path/to/original/cyoa_downloader.py
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _top_callables(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
    return sorted(set(names))


def _load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _normalized_signature(obj: Any):
    try:
        sig = inspect.signature(obj)
    except Exception as exc:  # pragma: no cover - report helper
        return ("ERR", type(exc).__name__)
    items = []
    for name, param in sig.parameters.items():
        default = "<empty>" if param.default is inspect._empty else repr(param.default)
        items.append((name, str(param.kind), default))
    return tuple(items)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("Usage: audit_original_parity.py /path/to/original/cyoa_downloader.py", file=sys.stderr)
        return 2
    original_path = pathlib.Path(argv[0]).resolve()
    ref_path = ROOT / "cyoa_downloader.py"
    names = _top_callables(original_path)
    original = _load_module("_cyoa_original_for_parity", original_path)
    sys.path.insert(0, str(ROOT))
    refactored = _load_module("_cyoa_refactored_for_parity", ref_path)

    missing = [name for name in names if not hasattr(refactored, name)]
    signature_diffs = []
    for name in names:
        if hasattr(original, name) and hasattr(refactored, name):
            lhs = _normalized_signature(getattr(original, name))
            rhs = _normalized_signature(getattr(refactored, name))
            if lhs != rhs:
                signature_diffs.append({"name": name, "original": lhs, "refactored": rhs})

    constants = [
        "_APP_VERSION", "_STABILIZATION_PATCH_ID", "IMAGE_FIELDS", "AUDIO_FIELDS",
        "DEFAULT_WAIT_TIME", "DEFAULT_MAX_WORKERS", "FONT_EXTENSIONS",
        "IMAGE_EXTENSIONS", "AUDIO_EXTENSIONS", "_BATCH_VALID_MODES",
    ]
    constant_diffs = []
    for name in constants:
        if getattr(original, name, None) != getattr(refactored, name, None):
            constant_diffs.append(name)

    result = {
        "original": str(original_path),
        "refactored": str(ref_path),
        "checked_callable_names": len(names),
        "missing_count": len(missing),
        "missing": missing,
        "signature_diff_count": len(signature_diffs),
        "signature_diffs": signature_diffs,
        "constant_diff_count": len(constant_diffs),
        "constant_diffs": constant_diffs,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if missing or signature_diffs or constant_diffs else 0


if __name__ == "__main__":
    raise SystemExit(main())
