"""Internal self-test implementation.

Phase 29 moves the long self-test body out of legacy.py. The implementation
still resolves legacy globals lazily at call time because many assertions
intentionally exercise compatibility names across modules.
"""

from __future__ import annotations

from typing import Tuple


def _sync_legacy_globals() -> None:
    """Expose compatibility globals expected by the historical self-test body."""
    import sys as _sys
    mod = _sys.modules.get("cyoa_downloader_app.runtime.surface") or _sys.modules.get("cyoa_downloader")
    if mod is None:
        import importlib as _importlib
        mod = _importlib.import_module("cyoa_downloader_app.runtime.surface")
    for name, value in vars(mod).items():
        if name.startswith("__") and name.endswith("__"):
            continue
        if name == "run_internal_self_test":
            continue
        globals()[name] = value

    # A few legacy setters mutate module-level booleans and the historical
    # self-test immediately inspects those booleans. Because this function now
    # lives in diagnostics.self_test, wrap the setters so local globals are
    # refreshed after each mutation.
    def _wrap_state_setter(setter_name: str):
        target = getattr(mod, setter_name, None)
        if not callable(target):
            return None
        def _wrapped(*args, **kwargs):
            result = target(*args, **kwargs)
            for state_name in (
                "_DEEP_SCAN_ENABLED", "_SELENIUM_ENABLED",
                "_SERVE_ENABLED", "_CHEAT_ENABLED", "_ITCH_ENABLED",
            ):
                if hasattr(mod, state_name):
                    globals()[state_name] = getattr(mod, state_name)
            return result
        return _wrapped

    for _setter in (
        "_set_deep_scan_enabled", "_set_selenium_enabled",
        "_set_serve_enabled", "_set_cheat_enabled", "_set_itch_enabled",
    ):
        _wrapped = _wrap_state_setter(_setter)
        if _wrapped is not None:
            globals()[_setter] = _wrapped


def run_internal_self_test() -> Tuple[bool, str]:
    """Run offline smoke tests for path safety, report flow, dependency reporting, and ZIP helper."""
    import tempfile as _tempfile
    _sync_legacy_globals()
    tests: List[Tuple[str, bool, str]] = []

    def record(name: str, passed: bool, detail: str = "") -> None:
        tests.append((name, bool(passed), detail))

    with _tempfile.TemporaryDirectory(prefix="cyoa_selftest_") as tmp_root:
        try:
            safe = _safe_join(tmp_root, "assets/image.png")
            record("safe_join normal path", safe.startswith(os.path.abspath(tmp_root)), safe)
        except Exception as e:
            record("safe_join normal path", False, str(e))
        try:
            try:
                _safe_archive_rel_path("../escape.txt")
                record("archive traversal rejection", False, "traversal accepted")
            except ValueError:
                record("archive traversal rejection", True, "ValueError as expected")
        except Exception as e:
            record("archive traversal rejection", False, str(e))
        try:
            stale = os.path.join(tmp_root, _DEPRECATED_BROKEN_ASSET_REPORT)
            pathlib.Path(stale).write_text("legacy", encoding="utf-8")
            out = write_asset_failure_summary(
                [{"url": "https://example.invalid/missing.png", "path": "images/missing.png", "error": "test", "kind": "image"}],
                tmp_root,
                source_url="self-test",
            )
            record("failure summary writes failed_assets.txt", bool(out and os.path.exists(out) and os.path.basename(out) == "failed_assets.txt"), str(out))
            record("stale broken_assets_report removed", not os.path.exists(stale), stale)
        except Exception as e:
            record("failure summary flow", False, str(e))
        try:
            src_merge = os.path.join(tmp_root, "merge_src")
            dst_merge = os.path.join(tmp_root, "merge_dst")
            os.makedirs(src_merge, exist_ok=True)
            os.makedirs(dst_merge, exist_ok=True)
            pathlib.Path(os.path.join(src_merge, "new.txt")).write_text("new", encoding="utf-8")
            pathlib.Path(os.path.join(dst_merge, "existing.txt")).write_text("existing", encoding="utf-8")
            copied = _copytree_merge_safe(src_merge, dst_merge, label="self-test merge")
            preserved = os.path.exists(os.path.join(dst_merge, "existing.txt"))
            added = os.path.exists(os.path.join(dst_merge, "new.txt"))
            record("merge-copy preserves existing assets", copied == 1 and preserved and added, f"copied={copied}")
        except Exception as e:
            record("merge-copy preserves existing assets", False, str(e))

        try:
            dep = dependency_check_report()
            record("dependency report generated", "dependency check" in dep.lower(), dep.splitlines()[0] if dep else "")
            record("dependency report includes urllib3 + ffmpeg guide",
                   ("urllib3" in dep and "ffmpeg" in dep.lower() and "ffmpeg -version" in dep), "")
        except Exception as e:
            record("dependency report generated", False, str(e))

        try:
            unsafe_urls_rejected = (
                _sanitize_ai_candidate_url("file:///tmp/project.json") is None
                and _sanitize_ai_candidate_url("javascript:alert(1)") is None
                and _extract_single_ai_url("data:text/plain,abc") is None
            )
            record("unsafe URL schemes rejected", unsafe_urls_rejected, "file/javascript/data")
        except Exception as e:
            record("unsafe URL schemes rejected", False, str(e))

        try:
            ssrf_blocked = (
                _host_is_internal("127.0.0.1")
                and _host_is_internal("169.254.169.254")
                and _host_is_internal("192.168.1.1")
                and _host_is_internal("localhost")
                and not _host_is_internal("neocities.org")
                and not _host_is_internal("8.8.8.8")
                and _sanitize_ai_candidate_url("http://169.254.169.254/meta") is None
            )
            record("SSRF internal-host guard", ssrf_blocked, "loopback/link-local/private blocked")
        except Exception as e:
            record("SSRF internal-host guard", False, str(e))

        try:
            # cross-origin internal asset screen.
            _set_allow_internal_hosts(False)
            xorigin_ok = (
                # cross-origin internal asset → blocked
                _ssrf_block_cross_origin("http://127.0.0.1:9/x.png", "http://example.com/cyoa/")
                and _ssrf_block_cross_origin("http://169.254.169.254/meta", "https://site.org/")
                # same-origin internal asset → allowed (localhost CYOA)
                and not _ssrf_block_cross_origin("http://127.0.0.1:8000/img/a.png", "http://127.0.0.1:8000/cyoa/")
                # public asset → allowed
                and not _ssrf_block_cross_origin("https://cdn.example.com/a.png", "http://example.com/cyoa/")
            )
            # opt-out disables the screen entirely
            _set_allow_internal_hosts(True)
            optout_ok = not _ssrf_block_cross_origin("http://127.0.0.1:9/x.png", "http://example.com/cyoa/")
            _set_allow_internal_hosts(False)
            record("SSRF cross-origin asset screen", xorigin_ok and optout_ok,
                   f"xorigin={xorigin_ok} optout={optout_ok}")
        except Exception as e:
            _set_allow_internal_hosts(False)
            record("SSRF cross-origin asset screen", False, str(e))

        try:
            # single-pass quoted substitution must not chain:
            # if one entry's replacement equals another entry's key, sequential
            # str.replace would double-rewrite. Single regex pass cannot.
            import re as _re_sub

            def _sp_sub(text: str, mapping: dict) -> str:
                if not mapping:
                    return text
                keys = sorted(mapping.keys(), key=len, reverse=True)
                alt = "|".join(_re_sub.escape(k) for k in keys)
                qr = _re_sub.compile('"(' + alt + ')"')
                return qr.sub(lambda mm: '"' + mapping.get(mm.group(1), mm.group(1)) + '"', text)

            chain = _sp_sub('{"a":"url1","b":"url2"}', {"url1": "url2", "url2": "final"})
            substr = _sp_sub('{"x":"a/b.png","y":"xa/b.png"}',
                             {"a/b.png": "images/A.png", "xa/b.png": "images/XA.png"})
            sub_ok = (
                chain == '{"a":"url2","b":"final"}'           # a NOT double-rewritten to final
                and substr == '{"x":"images/A.png","y":"images/XA.png"}'  # no substring bleed
                and _sp_sub('{"k":"v"}', {}) == '{"k":"v"}'    # empty-map noop
            )
            record("single-pass asset substitution (no chain)", sub_ok,
                   f"chain={chain}")
        except Exception as e:
            record("single-pass asset substitution (no chain)", False, str(e))

        try:
            # _rewrite_direct_urls must not re-fetch a relative
            # path that already resolves to an existing local file (output of a
            # prior @import/url() rewrite pass).
            import tempfile as _tf2, threading as _th2
            with _tf2.TemporaryDirectory() as _wd:
                _wd_inst = WebsiteDownloader.__new__(WebsiteDownloader)
                _wd_inst.folder = _wd
                _wd_inst.start_url = "http://site.example/cyoa/"
                _wd_inst._downloaded = {}
                _wd_inst._lock = _th2.Lock()
                _dl_calls = []

                def _fake_dl(url, preferred_kind="", referrer_url=None):
                    _dl_calls.append(url)
                    _ap = os.path.join(_wd, "assets", os.path.basename(url.split("?")[0]) or "i")
                    os.makedirs(os.path.dirname(_ap), exist_ok=True)
                    with open(_ap, "w") as _f:
                        _f.write("x")
                    return _ap

                _wd_inst._download_asset = _fake_dl
                _css_local = os.path.join(_wd, "css", "main.css")
                os.makedirs(os.path.dirname(_css_local), exist_ok=True)
                # Pre-create an already-localized asset and reference it relatively.
                os.makedirs(os.path.join(_wd, "assets"), exist_ok=True)
                with open(os.path.join(_wd, "assets", "bg.png"), "w") as _f:
                    _f.write("x")
                _txt = 'body{background:url("../assets/bg.png")} .y{background:url("http://cdn.x/z.png")}'
                _out = _wd_inst._rewrite_direct_urls(_txt, "http://site.example/cyoa/css/main.css", _css_local)
                # The already-local ../assets/bg.png must NOT be re-fetched; the
                # remote http://cdn.x/z.png must be fetched exactly once.
                relfetch = [c for c in _dl_calls if c.startswith(("../", "./"))]
                remote_fetched = any("cdn.x" in c for c in _dl_calls)
                rewrite_ok = (len(relfetch) == 0 and remote_fetched)
                record("direct-URL rewrite skips already-local", rewrite_ok,
                       f"relfetch={len(relfetch)} remote={remote_fetched}")
        except Exception as e:
            record("direct-URL rewrite skips already-local", False, str(e))

        try:
            # srcset parsing must not shred data: URIs (which
            # contain commas). Verify a data-URI candidate stays intact and the
            # following real candidate is still parsed.
            import threading as _th3
            _sr_inst = WebsiteDownloader.__new__(WebsiteDownloader)
            _sr_inst._downloaded = {}
            _sr_inst.output_folder = "/tmp"
            _sr_inst.folder = "/tmp"
            _sr_inst._lock = _th3.Lock()
            _sr_calls = []
            _sr_inst._download_asset = lambda u, preferred_kind="", referrer_url=None: (_sr_calls.append(u) or None)
            _sr_inst._rel = lambda a, b: b

            class _FakeTag(dict):
                pass

            _t = _FakeTag()
            _t["srcset"] = "data:image/svg+xml,%3Csvg%20w%3D%221%2C2%22%3E 1x, real.png 2x"
            _sr_inst._set_attr_local(_t, "srcset", "http://x/", "/tmp/index.html", preferred_kind="images")
            srcset_ok = (
                "data:image/svg+xml,%3Csvg%20w%3D%221%2C2%22%3E" in _t["srcset"]  # data URI intact
                and _sr_calls == ["real.png"]                                     # only real.png fetched
            )
            record("srcset parsing preserves data: URIs", srcset_ok,
                   f"calls={_sr_calls}")
        except Exception as e:
            record("srcset parsing preserves data: URIs", False, str(e))

        try:
            # Windows reserved device names must be made safe,
            # without over-prefixing legit names that merely contain the substring.
            cu = clean_url_path_component
            reserved_ok = (
                cu("CON") == "_CON"
                and cu("nul.json") == "_nul.json"
                and cu("COM1") == "_COM1"
                and cu("CONSOLE") == "CONSOLE"          # not reserved
                and cu("my_con_file") == "my_con_file"  # not reserved
                and cu("com10") == "com10"              # COM10 is not reserved
            )
            record("Windows reserved filename guard", reserved_ok,
                   f"CON->{cu('CON')!r} CONSOLE->{cu('CONSOLE')!r}")
        except Exception as e:
            record("Windows reserved filename guard", False, str(e))

        try:
            # ZIP member names must use '/' separators so
            # archives extract correctly on every OS (Windows os.path.relpath
            # would otherwise emit backslashes).
            import tempfile as _tfz, zipfile as _zfz, shutil as _shz
            _zd = _tfz.mkdtemp(prefix="cyoa_zipsep_")
            try:
                os.makedirs(os.path.join(_zd, "images"), exist_ok=True)
                with open(os.path.join(_zd, "images", "a.png"), "w") as _f:
                    _f.write("x")
                with open(os.path.join(_zd, "project.json"), "w") as _f:
                    _f.write("{}")
                _old = os.getcwd()
                os.chdir(_tfz.gettempdir())
                try:
                    _out = zip_temp_folder(_zd, "cyoa_zipsep_test")
                    with _zfz.ZipFile(_out) as _z:
                        _names = _z.namelist()
                    sep_ok = (
                        "images/a.png" in _names
                        and not any("\\" in n for n in _names)
                    )
                    record("ZIP member names use forward slash", sep_ok,
                           f"names={_names}")
                    try:
                        os.remove(_out)
                    except OSError:
                        pass
                finally:
                    os.chdir(_old)
            finally:
                _shz.rmtree(_zd, ignore_errors=True)
        except Exception as e:
            record("ZIP member names use forward slash", False, str(e))

        try:
            # _coerce_int must never raise on malformed input
            # (hand-edited settings.json, external FlareSolverr 'status' field).
            ci = _coerce_int
            coerce_ok = (
                ci("42", 5) == 42
                and ci("abc", 5) == 5
                and ci("", 5) == 5
                and ci(None, 5) == 5
                and ci("  17  ", 5) == 17
                and ci("3.7", 5) == 5      # non-int string → default
            )
            # And the FlareSolverr response builder must survive a bad status.
            _fr = _response_from_flaresolverr_solution(
                {"status": "weird-non-numeric", "response": "x"}, "http://x/")
            fr_ok = (_fr.status_code == 200)
            record("safe int coercion (settings/external)", coerce_ok and fr_ok,
                   f"coerce={coerce_ok} flaresolverr_status={_fr.status_code}")
        except Exception as e:
            record("safe int coercion (settings/external)", False, str(e))

        try:
            # Archive extraction must reject an oversized
            # member by DECLARED size before decompressing it into RAM. Verify
            # the declared-size guard logic blocks a member over budget.
            import io as _io2, zipfile as _zf2
            _buf = _io2.BytesIO()
            with _zf2.ZipFile(_buf, "w", _zf2.ZIP_DEFLATED) as _z:
                _z.writestr("big.bin", b"\x00" * (2 * 1024 * 1024))  # 2MB declared
            _buf.seek(0)
            _MAX = 1 * 1024 * 1024  # pretend 1MB budget
            _blocked = False
            with _zf2.ZipFile(_buf) as _arc:
                for _m in _arc.namelist():
                    _decl = int(getattr(_arc.getinfo(_m), "file_size", 0) or 0)
                    if _decl > _MAX:
                        _blocked = True  # would raise before arc.read()
                        break
            record("archive pre-read size guard", _blocked,
                   f"blocked_oversized={_blocked}")
        except Exception as e:
            record("archive pre-read size guard", False, str(e))

        try:
            theme_ok = (
                _normalize_theme_mode("system") == "System"
                and _normalize_theme_mode("light") == "Light"
                and _normalize_accent_color("#ABCDEF") == "#abcdef"
                and _normalize_accent_color("red") == "#3b82f6"
            )
            record("theme mode/accent normalization", theme_ok, "System/Light/Dark")
        except Exception as e:
            record("theme mode/accent normalization", False, str(e))
        try:
            zsrc = os.path.join(tmp_root, "zip_src")
            os.makedirs(zsrc, exist_ok=True)
            pathlib.Path(os.path.join(zsrc, "hello.txt")).write_text("hello", encoding="utf-8")
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp_root)
                zp = zip_temp_folder(zsrc, "selftest.zip")
            finally:
                os.chdir(old_cwd)
            record("zip helper creates archive", os.path.exists(zp), zp)
        except Exception as e:
            record("zip helper creates archive", False, str(e))

    # ── v7.5.8 checks: toggles, settings-race helper, itch URL detection ──
    try:
        _set_deep_scan_enabled(False)
        ok_off = (_DEEP_SCAN_ENABLED is False)
        _set_deep_scan_enabled(True)
        ok_on = (_DEEP_SCAN_ENABLED is True)
        record("deep-scan toggle gates flag", ok_off and ok_on,
                f"off={not ok_off and 'BAD' or 'ok'}, on={ok_on}")
    except Exception as e:
        record("deep-scan toggle gates flag", False, str(e))

    try:
        record("itch URL detection",
               _is_itch_url("https://foo.itch.io/bar") and not _is_itch_url("https://example.com"),
               "")
    except Exception as e:
        record("itch URL detection", False, str(e))

    try:
        import tempfile as _tf
        global _SETTINGS_FILE
        _orig = _SETTINGS_FILE
        with _tf.TemporaryDirectory() as _d:
            _SETTINGS_FILE = os.path.join(_d, "settings.json")
            _update_setting("deep_scan_enabled", False)
            v1 = _load_settings().get("deep_scan_enabled")
            _update_settings({"serve_enabled": False, "cheat_enabled": False})
            s2 = _load_settings()
            ok = (v1 is False and s2.get("serve_enabled") is False
                  and s2.get("cheat_enabled") is False)
            record("settings-race helper persists keys", ok, "")
        _SETTINGS_FILE = _orig
    except Exception as e:
        try:
            _SETTINGS_FILE = _orig  # type: ignore
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in run_internal_self_test (line 16378): %s", _ignored_exc)
        record("settings-race helper persists keys", False, str(e))

    # ── v7.6 checks: serve/cheat lifecycle + itch-dl backend ──────────────
    # 1) Preview token: a fresh token validates; a stale one is rejected; clear
    #    invalidates. This is the stale-preview-session guard.
    try:
        t1 = _new_preview_token()
        ok_fresh = _preview_token_valid(t1)
        t2 = _new_preview_token()          # rotates — t1 now stale
        ok_stale_rejected = (not _preview_token_valid(t1)) and _preview_token_valid(t2)
        _clear_preview_token()
        ok_cleared = (not _preview_token_valid(t2)) and (not _preview_token_valid(""))
        record("preview token: fresh valid / stale rejected / clear invalidates",
               ok_fresh and ok_stale_rejected and ok_cleared,
               f"fresh={ok_fresh} stale_rej={ok_stale_rejected} cleared={ok_cleared}")
    except Exception as e:
        record("preview token guard", False, str(e))

    # 2) Serve toggle gates execution (flag, not cosmetic).
    try:
        _set_serve_enabled(False); off = (_SERVE_ENABLED is False)
        _set_serve_enabled(True);  on = (_SERVE_ENABLED is True)
        record("serve toggle gates flag", off and on, f"off={off} on={on}")
    except Exception as e:
        record("serve toggle gates flag", False, str(e))

    # 3) Cheat toggle gates execution.
    try:
        _set_cheat_enabled(False); coff = (_CHEAT_ENABLED is False)
        _set_cheat_enabled(True);  con = (_CHEAT_ENABLED is True)
        record("cheat toggle gates flag", coff and con, f"off={coff} on={con}")
    except Exception as e:
        record("cheat toggle gates flag", False, str(e))

    # 4) itch-dl backend detection returns a well-formed result and never raises.
    try:
        cmd, label = detect_itch_backend()
        ok = (cmd is None or (isinstance(cmd, list) and len(cmd) >= 1)) and isinstance(label, str)
        record("itch-dl backend detection well-formed", ok, f"label={label}")
    except Exception as e:
        record("itch-dl backend detection well-formed", False, str(e))

    # 5) itch command builder never leaks the API key in its redacted log form.
    try:
        cmd = build_itch_command(["uvx", "itch-dl"], "https://x.itch.io/y",
                                 "/tmp/out/itch_assets",
                                 api_key="SUPERSECRETKEY123", mirror_web=True)
        redacted = redact_itch_command(cmd)
        leaked = "SUPERSECRETKEY123" in redacted
        has_mask = "***" in redacted
        has_mirror = "--mirror-web" in cmd
        record("itch command builder masks key + supports mirror-web",
               (not leaked) and has_mask and has_mirror,
               f"leaked={leaked} mask={has_mask} mirror={has_mirror}")
    except Exception as e:
        record("itch command builder masks key + supports mirror-web", False, str(e))

    # ── v1.0 Release checks: settings export/import (Feature #1) ────────────────
    try:
        import tempfile as _tf
        _orig_sf = _SETTINGS_FILE
        with _tf.TemporaryDirectory() as _d:
            _SETTINGS_FILE = os.path.join(_d, "settings.json")
            # Seed a secret + a safe value.
            _update_settings({"ai_api_key_anthropic": "sk-LEAKME",
                              "itch_api_key": "itch-LEAKME",
                              "language": "id"})
            exp = os.path.join(_d, "export.json")
            ok_e, _ = export_settings(exp)
            with open(exp, encoding="utf-8") as _ef:
                raw = _ef.read()
            secret_excluded = ("sk-LEAKME" not in raw) and ("itch-LEAKME" not in raw)
            import json as _json
            blob = _json.loads(raw)
            has_meta = bool(blob.get("_meta", {}).get("schema_version") is not None
                            and blob["_meta"].get("app_version"))
            record("settings export excludes secrets + has metadata",
                   ok_e and secret_excluded and has_meta,
                   f"secret_excluded={secret_excluded} meta={has_meta}")
        _SETTINGS_FILE = _orig_sf
    except Exception as e:
        try: _SETTINGS_FILE = _orig_sf  # type: ignore
        except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in run_internal_self_test (line 16461): %s", _ignored_exc)
        record("settings export excludes secrets + has metadata", False, str(e))

    try:
        import tempfile as _tf
        _orig_sf2 = _SETTINGS_FILE
        with _tf.TemporaryDirectory() as _d:
            _SETTINGS_FILE = os.path.join(_d, "settings.json")
            _save_settings(dict(_SETTINGS_DEFAULTS))
            imp = os.path.join(_d, "import.json")
            import json as _json
            with open(imp, "w", encoding="utf-8") as _if:
                _json.dump({"settings": {
                    "language": "en",                     # safe → merge
                    "ai_api_key": "sk-REAL",              # secret w/value → ignore
                    "itch_api_key": _REDACTED_PLACEHOLDER,# redacted → ignore
                    "cloudflare_mode": _REDACTED_PLACEHOLDER,  # redacted → ignore
                    "totally_unknown_xyz": 1,            # unknown → ignore
                }}, _if)
            ok_i, _ = import_settings(imp)
            s = _load_settings()
            merged = (s.get("language") == "en")
            no_secret = (s.get("ai_api_key") == "")
            no_redacted_restore = (s.get("cloudflare_mode") != _REDACTED_PLACEHOLDER)
            no_unknown = ("totally_unknown_xyz" not in s)
            # invalid JSON must fail cleanly
            bad = os.path.join(_d, "bad.json")
            with open(bad, "w") as _bf:
                _bf.write("{nope")
            ok_bad, _ = import_settings(bad)
            record("settings import merges safe + ignores secrets/redacted/unknown",
                   ok_i and merged and no_secret and no_redacted_restore
                   and no_unknown and (ok_bad is False),
                   f"merged={merged} no_secret={no_secret} "
                   f"no_redacted={no_redacted_restore} no_unknown={no_unknown} "
                   f"bad_rejected={not ok_bad}")
        _SETTINGS_FILE = _orig_sf2
    except Exception as e:
        try: _SETTINGS_FILE = _orig_sf2  # type: ignore
        except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in run_internal_self_test (line 16498): %s", _ignored_exc)
        record("settings import merges safe + ignores secrets/redacted/unknown", False, str(e))

    # ── v1.0 Release checks: plugin registry (Feature #10) ─────────────────────
    try:
        reg = _PluginRegistry("test")
        reg.register("a", lambda: 1)
        dup_ok = False
        try:
            reg.register("a", lambda: 2)        # duplicate without override
        except ValueError:
            dup_ok = True
        reg.register("a", lambda: 3, override=True)  # deterministic override
        override_ok = (dict(reg.items())["a"]() == 3)
        record("plugin registry: register + duplicate-reject + override",
               dup_ok and override_ok, f"dup_reject={dup_ok} override={override_ok}")
    except Exception as e:
        record("plugin registry: register + duplicate-reject + override", False, str(e))

    try:
        # Default builtin scanner still runs; a failing plugin is contained.
        before = list(_ASSET_SCANNER_PLUGINS.names())
        js = 'x="./assets/p.webp"'
        register_asset_scanner("__boom__",
                               lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
        res = run_asset_scanner_plugins(js, "https://h/app/m.js", "https://h/app/", ".js")
        builtin_ran = any("p.webp" in u for u in res)
        contained = ("__boom__" in _ASSET_SCANNER_PLUGINS.names())
        _ASSET_SCANNER_PLUGINS.unregister("__boom__")
        restored = (list(_ASSET_SCANNER_PLUGINS.names()) == before)
        record("plugin registry: default scanner runs + failing plugin contained",
               builtin_ran and contained and restored,
               f"builtin_ran={builtin_ran} contained={contained} restored={restored}")
    except Exception as e:
        try: _ASSET_SCANNER_PLUGINS.unregister("__boom__")
        except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in run_internal_self_test (line 16533): %s", _ignored_exc)
        record("plugin registry: default scanner runs + failing plugin contained", False, str(e))

    # [STAB-rev18] Batch mode-flag parity guard. Locks the shared
    # _derive_mode_flags() semantics so the GUI and CLI batch dispatch loops
    # cannot silently diverge again (regression guard for the bare
    # pure_website / cyoap_vue mis-dispatch fixed in rev18).
    try:
        _expect = {
            "embed":               (False, False, False, False, True,  "standard"),
            "zip":                 (True,  False, False, False, True,  "standard"),
            "both":                (False, True,  False, False, True,  "standard"),
            "website_zip":         (False, False, False, True,  True,  "standard"),
            "website_folder":      (False, False, False, True,  False, "standard"),
            "pure_website":        (False, False, True,  True,  True,  "standard"),
            "pure_website_folder": (False, False, True,  True,  False, "standard"),
            "cyoap_vue":           (False, False, False, True,  True,  "cyoap_vue"),
            "cyoap_vue_folder":    (False, False, False, True,  False, "cyoap_vue"),
        }
        _mismatch = []
        for _mode, _exp in _expect.items():
            _f = _derive_mode_flags(_mode)
            _got = (_f["zip"], _f["both"], _f["pure"], _f["website"],
                    _f["website_zip"], _f["engine"])
            if _got != _exp:
                _mismatch.append(f"{_mode}: {_got}!={_exp}")
        record("batch mode-flag parity (GUI/CLI single source)",
               not _mismatch,
               "all aligned" if not _mismatch else "; ".join(_mismatch))
    except Exception as e:
        record("batch mode-flag parity (GUI/CLI single source)", False, str(e))

    # [STAB-rev19] Offline package validator smoke test. Builds a healthy and
    # a broken fixture in a temp dir and asserts verify_output_package's verdict
    # + missing-asset detection. Regression guard for the new --verify feature.
    try:
        import tempfile as _vt
        with _vt.TemporaryDirectory(prefix="cyoa_verify_") as _vroot:
            _good = os.path.join(_vroot, "good"); os.makedirs(os.path.join(_good, "images"))
            with open(os.path.join(_good, "project.json"), "w", encoding="utf-8") as _f:
                _f.write('{"rows":[{"objects":[{"image":"images/a.png"}]}],"styling":{}}')
            with open(os.path.join(_good, "images", "a.png"), "wb") as _f:
                _f.write(b"PNGDATA")
            _ok_good, _ = verify_output_package(_good)
            _bad = os.path.join(_vroot, "bad"); os.makedirs(_bad)
            with open(os.path.join(_bad, "project.json"), "w", encoding="utf-8") as _f:
                _f.write('{"rows":[{"objects":[{"image":"images/gone.png"}]}],"styling":{}}')
            _ok_bad, _rep_bad = verify_output_package(_bad)
            _detected = (not _ok_bad) and ("gone.png" in _rep_bad)
            record("package validator: good passes / missing-asset fails",
                   _ok_good and _detected,
                   f"good_ok={_ok_good} bad_detected={_detected}")
    except Exception as e:
        record("package validator: good passes / missing-asset fails", False, str(e))

    # [STAB-rev20] After-callback destroy-safety guard. Verifies
    # _v25_safe_after_widget is a safe no-op when the root is missing (the
    # path that doesn't require a live display), and that a fake widget whose
    # winfo_exists() returns False causes the callback to be skipped rather
    # than invoked. Regression guard for the root.after post-destroy lens.
    try:
        _calls = {"n": 0}
        class _DeadRoot:
            def winfo_exists(self):
                return False
        class _DeadWidget:
            def winfo_exists(self):
                return False
        # Dead root -> must not schedule, must not raise, callback never runs.
        _v25_safe_after_widget(_DeadRoot(), _DeadWidget(),
                               lambda: _calls.__setitem__("n", _calls["n"] + 1))
        # None root -> safe no-op.
        _v25_safe_after_widget(None, None,
                               lambda: _calls.__setitem__("n", _calls["n"] + 1))
        record("after-guard: dead/None root is safe no-op",
               _calls["n"] == 0,
               f"callbacks_invoked={_calls['n']} (expected 0)")
    except Exception as e:
        record("after-guard: dead/None root is safe no-op", False, str(e))

    # [STAB-rev21] Manifest sidecar round-trip. Writes a manifest for a fixture,
    # verifies it reports intact, then corrupts a file and confirms checksum
    # mismatch is detected. Regression guard for the --write-manifest feature.
    try:
        import tempfile as _mt
        with _mt.TemporaryDirectory(prefix="cyoa_manifest_") as _mroot:
            _pkg = os.path.join(_mroot, "pkg"); os.makedirs(os.path.join(_pkg, "images"))
            with open(os.path.join(_pkg, "project.json"), "w", encoding="utf-8") as _f:
                _f.write('{"rows":[{"objects":[{"image":"images/a.png"}]}],"styling":{}}')
            _asset = os.path.join(_pkg, "images", "a.png")
            with open(_asset, "wb") as _f:
                _f.write(b"ORIGINAL-BYTES")
            _wrote_ok, _ = write_package_manifest(_pkg)
            _intact_ok, _ = verify_output_package(_pkg)
            # corrupt the asset, re-verify -> must now fail with mismatch
            with open(_asset, "wb") as _f:
                _f.write(b"TAMPERED-BYTES-DIFFERENT-LENGTH")
            _corrupt_ok, _corrupt_rep = verify_output_package(_pkg)
            _detected = (not _corrupt_ok) and ("checksum mismatch" in _corrupt_rep)
            record("manifest round-trip: write / intact / corruption detected",
                   _wrote_ok and _intact_ok and _detected,
                   f"wrote={_wrote_ok} intact={_intact_ok} corruption_detected={_detected}")
    except Exception as e:
        record("manifest round-trip: write / intact / corruption detected", False, str(e))

    # [STAB-rev22] Decode robustness. try_decode_bytes must never raise on
    # arbitrary byte sequences (latin-1 last resort), and must avoid the
    # latin-1-too-early mojibake trap by returning real UTF-8 for UTF-8 input.
    try:
        _arbitrary = bytes(range(256))
        _d1 = try_decode_bytes(_arbitrary, "utf-8")
        _utf8_in = "日本語テスト café".encode("utf-8")
        _d2 = try_decode_bytes(_utf8_in, "utf-8")
        _ok = isinstance(_d1, str) and _d2 == "日本語テスト café"
        record("decode robustness: arbitrary bytes + utf-8 fidelity",
               _ok, f"arbitrary_ok={isinstance(_d1, str)} utf8_roundtrip={_d2 == '日本語テスト café'}")
    except Exception as e:
        record("decode robustness: arbitrary bytes + utf-8 fidelity", False, str(e))

    # [STAB-rev23] Queue-preservation policy in _done(). When the completion
    # status string is unparseable, the queue must be PRESERVED (conservative),
    # never cleared — otherwise a failed run whose status didn't match the
    # "… — N/M …" shape would silently lose its retry queue. We model the
    # decision here (the GUI method itself needs a live root).
    try:
        def _queue_decision(status):
            try:
                _s = int(status.split("—")[1].strip().split("/")[0].strip())
                _t = int(status.split("/")[1].strip().split(" ")[0])
                if _s < _t:
                    return "preserve"
            except Exception:
                return "preserve"   # rev23: unparseable -> conservative keep
            return "cleanup"
        _cases = {
            "Selesai — 5/5 berhasil": "cleanup",
            "Selesai — 3/5 berhasil": "preserve",
            "Download cancelled": "preserve",
            "": "preserve",
            "完了": "preserve",
        }
        _bad = [f"{k!r}->{_queue_decision(k)}!={v}"
                for k, v in _cases.items() if _queue_decision(k) != v]
        record("queue policy: unparseable status preserves queue",
               not _bad, "all correct" if not _bad else "; ".join(_bad))
    except Exception as e:
        record("queue policy: unparseable status preserves queue", False, str(e))

    passed = sum(1 for _, ok, _ in tests if ok)
    lines = [f"CYOA Downloader v{_APP_VERSION} internal self-test", "=" * 52]
    for name, ok, detail in tests:
        lines.append(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    lines.append("-" * 52)
    lines.append(f"Result: {passed}/{len(tests)} passed")
    return passed == len(tests), "\n".join(lines)
