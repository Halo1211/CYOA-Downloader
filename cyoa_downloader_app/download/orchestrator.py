"""Download orchestration domain implementation.

Phase 40 moves the base run_download implementation out of legacy.py. The
historical v46.2/v46.6 wrappers still live in legacy during this transition, so
this module exposes both the moved base implementation and aliases to the final
patched public entry point.
"""

from __future__ import annotations

from ._bridge import legacy as _legacy
from .archive_policy import ArchivePolicy
from .archive_profiler import project_archive_profile
from .archive_runner import run_archive_extensions
from .cyoa_cafe_static import download_cyoa_cafe_static_record
from ..app_info import DEFAULT_MAX_WORKERS
from ..runtime.state import _RUN_DOWNLOAD_LOCK
from ..gui.final_behaviors import (
    _v462_resolve_pure_download_url,
    _v462_run_download,
    _v466_run_download,
)
from ..project.parse import _ARCHIVE_ORG_CYOA_RE
from ..project.cyoa_cafe import classify_cyoa_cafe_record, fetch_cyoa_cafe_record

_PROTECTED = {
    "_sync_legacy_globals", "_set_last_preview_folder", "_base_run_download",
    "run_download", "_v462_resolve_pure_download_url", "_v462_run_download",
    "_v466_run_download", "_RUN_DOWNLOAD_LOCK", "_LAST_PREVIEW_FOLDER",
}


def _sync_legacy_globals():
    """Refresh moved legacy bodies with the latest compatibility namespace."""
    l = _legacy()
    for name, value in vars(l).items():
        if name.startswith("__"):
            continue
        if name not in _PROTECTED:
            globals()[name] = value
    return l


def _set_last_preview_folder(value):
    global _LAST_PREVIEW_FOLDER
    _LAST_PREVIEW_FOLDER = value
    try:
        _legacy()._LAST_PREVIEW_FOLDER = value
    except Exception:
        pass


def _base_run_download(
    url: str,
    file_name: str = "",
    zip_output: bool = False,
    both_output: bool = False,
    website_output: bool = False,
    website_zip_output: bool = True,
    pure_website: bool = False,
    download_fonts: bool = False,
    show_font_analysis: bool = True,
    output_dir: str = "",
    max_workers: int = DEFAULT_MAX_WORKERS,
    engine_mode: str = "standard",
    cyoa_mgr_enabled: bool = False,
    ai_api_key: str = "",
    ai_provider: str = "",
    ai_mode: str = "auto_fallback",
    analysis_only: bool = False,
    archive_strategy: str = "",
    archive_max_pages: int = 0,
    archive_max_depth: int = -1,
    archive_capture_interactions: bool = False,
) -> None:
    """
    Main download orchestrator.

    pure_website=True: skip project.json search entirely — just download
    the viewer HTML/CSS/JS/assets. Useful for custom-format sites like
    lewd_horizon that don't use a standard ICC project file.
    """
    global wait_time, _LAST_PREVIEW_FOLDER
    _sync_legacy_globals()
    _set_last_preview_folder(None)
    # Clamp worker count at the single CLI/programmatic entry point.
    # The GUI already clamps via max(1, ...), but the CLI passed args.threads
    # straight through, so --threads 0 (or negative) reached
    # ThreadPoolExecutor(max_workers=0) and raised
    # "ValueError: max_workers must be greater than 0". Additive guard only;
    # download concept/inputs/outputs unchanged.
    try:
        max_workers = max(1, min(64, int(max_workers)))
    except (TypeError, ValueError):
        max_workers = DEFAULT_MAX_WORKERS
    ai_provider = _normalize_ai_provider(ai_provider or _get_ai_provider())
    ai_mode = _normalize_ai_mode(ai_mode or _load_settings().get("ai_mode", "auto_fallback"))
    ai_budget = AIUsageBudget()
    ai_available = _ai_is_available(ai_api_key, ai_provider) and ai_mode != "off"
    archive_settings = _load_settings()
    archive_policy = ArchivePolicy(
        strategy=archive_strategy or archive_settings.get("archive_strategy", "classic"),
        max_pages=archive_max_pages or archive_settings.get("archive_max_pages", 300),
        max_depth=(archive_max_depth if archive_max_depth >= 0 else archive_settings.get("archive_max_depth", 30)),
        capture_interactions=(archive_capture_interactions or archive_settings.get("archive_capture_interactions", False)),
        interaction_policy=archive_settings.get("archive_interaction_policy", "safe"),
        settle_time_ms=archive_settings.get("archive_settle_time_ms", 1800),
        runtime_max_pages=archive_settings.get("archive_runtime_max_pages", 12),
        max_scroll_steps=archive_settings.get("archive_max_scroll_steps", 100),
        max_interactions=archive_settings.get("archive_max_interactions", 20),
        no_progress_rounds=archive_settings.get("archive_no_progress_rounds", 2),
    ).normalized()

    # ── Disk space check (non-critical, just warn) ─────────────────────────
    try:
        target = os.path.abspath(output_dir) if output_dir else os.getcwd()
        if output_dir:
            os.makedirs(target, exist_ok=True)
        if hasattr(os, "statvfs"):
            st = os.statvfs(target)
            free_mb = (st.f_bavail * st.f_frsize) / (1024 * 1024)
            if free_mb < 100:
                logger.warning(
                    f"Disk hampir penuh! Sisa: {free_mb:.0f} MB. "
                    f"Download will continue but may fail midway."
                )
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in run_download (line 12626): %s", _ignored_exc)

    if not file_name:
        file_name = _build_output_name(url)

    # ── archive.org CYOA catalog URL → redirect to original site ───────────
    # CYOA Manager catalog links:
    # https://archive.org/download/CYOAZipArchive/Name.[date].https~~~site.com~path.zip
    _archive_m = _ARCHIVE_ORG_CYOA_RE.search(url)
    if _archive_m:
        zip_filename = _archive_m.group(1)
        original_site = _extract_website_from_archive_zip_name(zip_filename)
        if original_site:
            logger.info(
                f"archive.org catalog URL → using original site: {original_site}"
            )
            url = original_site
            if not file_name or file_name == "downloaded_cyoa":
                file_name = _build_output_name(original_site)
    if not file_name:
        file_name = "downloaded_cyoa"
    file_name = clean_url_path_component(file_name)

    _RUN_DOWNLOAD_LOCK.acquire()
    original_dir = os.getcwd()
    tmp = None  # pre-bind so finally cleanup is NameError-safe
    try:
        if output_dir:
            output_dir = os.path.abspath(output_dir)
            os.makedirs(output_dir, exist_ok=True)
            os.chdir(output_dir)
        else:
            output_dir = os.getcwd()

        # CYOA.CAFE records with cyoa_pages are authoritative static galleries,
        # not failed iframe resolutions. Export their PocketBase files directly
        # instead of mirroring the backend-dependent React catalogue shell.
        if website_output or pure_website:
            static_record = fetch_cyoa_cafe_record(url)
            if classify_cyoa_cafe_record(static_record) == "static_pages":
                site_folder = _unique_folder(file_name)
                logger.info("CYOA.CAFE static-page record detected → %s/", site_folder)
                prepare_clean_output_folder(site_folder)
                download_cyoa_cafe_static_record(
                    static_record,
                    site_folder,
                    source_url=url,
                    max_workers=max_workers,
                )
                _set_last_preview_folder(os.path.abspath(site_folder) if not website_zip_output else None)
                _finalize_site_folder(site_folder, file_name, website_zip_output)
                logger.info("CYOA.CAFE static archive complete.")
                return

        # ── Pure ICC mode: skip project search ─────────────────
        if pure_website:
            site_folder = _unique_folder(file_name)
            logger.info(f"Pure website download (no project search) → {site_folder}/")
            prepare_clean_output_folder(site_folder)
            viewer = WebsiteDownloader(url, site_folder, max_workers=max_workers, ai_api_key=ai_api_key, ai_provider=ai_provider, ai_mode=ai_mode, ai_budget=ai_budget, archive_strategy=archive_policy.strategy)
            viewer.download()
            run_archive_extensions(viewer, archive_policy)
            viewer.localize_existing_text_assets()
            # No backup_report since there's no project payload
            if download_fonts:
                # Only scan viewer HTML for fonts (no project.json to scan)
                viewer_html_pu = get_source(url) or ""
                _download_fonts_into_folder("", url, site_folder, html_source=viewer_html_pu)
            _set_last_preview_folder(os.path.abspath(site_folder) if not website_zip_output else None)
            _finalize_site_folder(site_folder, file_name, website_zip_output)
            logger.info("Pure website download complete.")
            return

        if website_output and engine_mode in {"cyoap_vue", "auto"}:
            logger.info("Phase 0/4: probing cyoap_vue dist/ structure…")
            site_folder = _unique_folder(file_name)
            try:
                if try_download_cyoap_vue_site(
                    url,
                    site_folder,
                    website_zip_output=website_zip_output,
                    max_workers=max_workers,
                ):
                    return
                if engine_mode == "cyoap_vue":
                    raise RuntimeError("cyoap_vue mode selected, but dist/platform.json + dist/nodes/list.json were not found.")
                logger.info("cyoap_vue probe: no dist/platform.json + dist/nodes/list.json pair found; falling back.")
            except Exception as e:
                if engine_mode == "cyoap_vue":
                    raise
                logger.warning(f"cyoap_vue auto probe failed, falling back to standard resolver: {e}")

        logger.info("Phase 1/4: resolving project source…")
        project_source, project_url = get_project_source(url, ai_api_key=ai_api_key, ai_provider=ai_provider, ai_mode=ai_mode, ai_budget=ai_budget)
        if not project_source:
            # ── AI viewer analysis (diagnostic) ────────────────────────
            ai_hint = ""
            if ai_available and _ai_mode_allows("diagnostics", ai_mode):
                _diag_resp = None
                try:
                    _diag_resp = fetch_response(url, timeout=15, extra_headers={"User-Agent": "Mozilla/5.0"})
                    _diag_html = _safe_response_text(_diag_resp) if _diag_resp is not None else ""
                    analysis = _ai_analyze_viewer_logic(
                        _diag_html, {}, url, api_key=ai_api_key, provider=ai_provider,
                        ai_mode=ai_mode, budget_obj=ai_budget)
                    if analysis:
                        viewer_type = analysis.get("viewer_type", "unknown")
                        data_src = analysis.get("data_source", "unknown")
                        suggestions = analysis.get("suggestions", [])
                        logger.info(f"[AI analysis] viewer={viewer_type}, data={data_src}")
                        for s in suggestions:
                            logger.info(f"  → {s}")
                        ai_hint = (
                            f"\n\nAI analysis: viewer_type={viewer_type}, "
                            f"data_source={data_src}\n"
                            + "\n".join(f"  → {s}" for s in suggestions[:5])
                        )
                except Exception as _ignored_exc:
                    logger.debug("Ignored recoverable exception in run_download (line 12721): %s", _ignored_exc)
                finally:
                    if _diag_resp is not None:
                        try:
                            _diag_resp.close()
                        except Exception:
                            pass

            raise RuntimeError(
                "Could not resolve project data (project.json / project.txt / embedded JS / zip payload).\n"
                "If this site uses a custom viewer without a standard project file,\n"
                "try using mode: ICC ZIP/Folder or --icc to download\n"
                "the viewer HTML/CSS/JS directly without needing a project file."
                + ai_hint
            )

        cleaned = normalize_project_payload_text(project_source)
        if not cleaned:
            raise RuntimeError(
                "Project source was found but is not valid JSON/JSON5 project data."
            )
        logger.info("Phase 2/4: project source resolved.")

        # ── Feature 4: Extract metadata ────────────────────────────────────
        try:
            _meta_obj = json.loads(cleaned) if cleaned.strip().startswith("{") else {}
            _meta_app = _meta_obj.get("app", _meta_obj)
            _rows  = _meta_app.get("rows", [])
            _bp    = _meta_app.get("backpack", [])
            _title = (
                _meta_app.get("title") or
                _meta_app.get("name") or
                _meta_app.get("projectTitle") or ""
            )
            _author = _meta_app.get("author") or _meta_app.get("authorName") or ""
            _meta_img_count = sum(
                1 for r in _rows
                for obj in r.get("objects", [])
                if obj.get("image")
            )
            _metadata = {
                "title":        _title,
                "author":       _author,
                "source_url":   url,
                "project_url":  project_url,
                "rows":         len(_rows),
                "objects_total": sum(len(r.get("objects", [])) for r in _rows),
                "backpack_slots": len(_bp),
                "images_referenced": _meta_img_count,
                "downloaded_at": __import__("datetime").datetime.now().isoformat(),
            }
            logger.info(
                f"Metadata: title={_title!r} rows={len(_rows)} "
                f"objects={_metadata['objects_total']} images={_meta_img_count}"
            )
        except Exception:
            _metadata = {"source_url": url, "project_url": project_url}


        if not file_name:
            file_name = clean_url_path_component(get_first_folder_from_url(project_url))
        if not file_name:
            file_name = clean_url_path_component(get_first_subdomain(project_url))
        if not file_name:
            file_name = "downloaded_cyoa"
        file_name = clean_url_path_component(file_name)

        base_url = strip_document_from_url(project_url)

        # v46.7: when the user supplied a CYOA.CAFE metadata route, project_url
        # belongs to the authoritative viewer host. Use that viewer root for all
        # website crawling/localization while preserving the original URL only
        # for naming/history. This is intentionally limited to /game/<id>.
        website_entry_url = url
        try:
            _entry_parsed = urlparse(canonicalize_url(url))
            _entry_path = re.sub(r"/+", "/", _entry_parsed.path or "/")
            _is_metadata_entry = (
                _entry_parsed.netloc.lower() == "cyoa.cafe"
                and bool(re.fullmatch(r"/game/[^/]+/?", _entry_path, flags=re.IGNORECASE))
            )
            _project_root = canonicalize_url(base_url)
            if _is_metadata_entry and _project_root:
                _project_host = urlparse(_project_root).netloc.lower()
                if _project_host and _project_host != "cyoa.cafe":
                    website_entry_url = _project_root
                    logger.info(
                        "CYOA.CAFE authoritative viewer selected for website crawl: "
                        f"{url} → {website_entry_url}"
                    )
        except Exception as _viewer_url_exc:
            logger.debug(f"Could not derive authoritative viewer URL: {_viewer_url_exc}")

        # Fetch viewer HTML once — reused for font scanning
        viewer_html: str = get_source(website_entry_url) or ""

        if show_font_analysis:
            analyse_fonts(cleaned, base_url, html_source=viewer_html)
            if analysis_only:
                logger.info("Analysis-only mode complete; no download output written.")
                return

        # ── Full ICC mode ───────────────────────────────────────
        if website_output:
            site_folder = _unique_folder(file_name)
            logger.info(f"Downloading full website → {site_folder}/")
            prepare_clean_output_folder(site_folder)

            viewer = WebsiteDownloader(website_entry_url, site_folder, max_workers=max_workers, ai_api_key=ai_api_key, ai_provider=ai_provider, ai_mode=ai_mode, ai_budget=ai_budget, archive_strategy=archive_policy.strategy)
            if archive_policy.strategy == "auto":
                viewer.archive_auto_profile = project_archive_profile(
                    website_entry_url, project_url,
                )
            viewer.download()
            run_archive_extensions(viewer, archive_policy)

            working = cleaned
            if download_fonts:
                # In ICC mode, WebsiteDownloader already downloads fonts from CSS/HTML.
                # We only scan project.json here (html_source="" avoids re-downloading
                # viewer HTML fonts that WebsiteDownloader already handled).
                working = _download_fonts_into_folder(
                    working, base_url, site_folder, html_source=""
                )

            tmp = create_random_temp_folder()
            try:
                _, dl_result, _pi_urls = process_images(
                    working, base_url,
                    embed=False, download=True,
                    temp_folder=tmp, wait_time=wait_time, max_workers=max_workers,
                    output_dir=output_dir, source_url=website_entry_url,
                    # WebsiteDownloader/deep-scan may already have saved the
                    # same project images into the ICC folder. Let the JSON
                    # pipeline resolve relative paths against that folder so
                    # it reuses those files instead of fetching every image a
                    # second time.
                    site_folder=site_folder,
                )
                img_src = os.path.join(tmp, "images")
                img_dst = os.path.join(site_folder, "images")
                if os.path.isdir(img_src):
                    _n_img = _copytree_merge_safe(img_src, img_dst, label="website images")
                    logger.info(f"Saved/merged: images/ ({_n_img} file(s))")
                # Also move audio folder if present. Merge, never delete an existing
                # website audio folder generated by viewer/deep-scan stages.
                audio_src = os.path.join(tmp, "audio")
                if os.path.isdir(audio_src):
                    audio_dst = os.path.join(site_folder, "audio")
                    _n_audio = _copytree_merge_safe(audio_src, audio_dst, label="website audio")
                    logger.info(f"Saved/merged: audio/ ({_n_audio} file(s))")

                # Save project_original.json only when URLs differ from raw
                if dl_result != cleaned:
                    save_string_to_file(cleaned, "project_original.json", site_folder)
                    logger.info("Saved: project_original.json (raw URLs before localization)")
                viewer.write_project_payload(project_url, dl_result)

                # The recursive website pass already localized HTML/CSS/JS.
                # Re-scanning those rewritten files in project-first Auto mode
                # would reinterpret local ``fonts/...`` paths as remote URLs and
                # generate duplicate 404 probes. Other modes keep the historical
                # recovery pass.
                if (
                    archive_policy.strategy == "auto"
                    and getattr(
                        getattr(viewer, "archive_auto_profile", None),
                        "detected_engine", "",
                    ) == "project_json"
                ):
                    logger.info(
                        "[Auto] Website text assets were already localized; "
                        "skipping duplicate recovery pass."
                    )
                else:
                    viewer.localize_existing_text_assets()

                # Deep scan: download any assets referenced in JS/CSS bundles
                # that were not referenced in project.json IMAGE_FIELDS
                if (
                    archive_policy.strategy == "auto"
                    and getattr(
                        getattr(viewer, "archive_auto_profile", None),
                        "detected_engine", "",
                    ) == "project_json"
                ):
                    logger.info(
                        "[Auto] Project assets were localized by process_images; "
                        "skipping redundant post-project JS/CSS deep scan."
                    )
                elif not _DEEP_SCAN_ENABLED:
                    logger.info("Deep scan disabled by toggle — skipping JS/CSS asset pass.")
                else:
                  _deep_scan_and_download_assets(
                    folder=site_folder,
                    base_url=base_url,
                    output_dir=output_dir,
                    ai_api_key=ai_api_key,
                    ai_provider=ai_provider,
                    ai_mode=ai_mode,
                    ai_budget=ai_budget,
                    skip_urls=_pi_urls,
                  )
            finally:
                delete_temp_folder(tmp)

            viewer.write_manifest(project_url=project_url)
            # Feature 6: integrity check
            integrity = viewer.validate_integrity()
            if integrity["missing"]:
                try:
                    report_path = os.path.join(site_folder, "backup_report.txt")
                    with open(report_path, "a", encoding="utf-8") as _rf:
                        _rf.write("\n" + "="*60 + "\n")
                        _rf.write("INTEGRITY CHECK — MISSING LOCAL REFS\n")
                        _rf.write("="*60 + "\n")
                        for miss in integrity["missing"]:
                            _rf.write(f"  {miss}\n")
                except Exception as _ignored_exc:
                    logger.debug("Ignored recoverable exception in run_download (line 12869): %s", _ignored_exc)
            _set_last_preview_folder(os.path.abspath(site_folder) if not website_zip_output else None)
            _finalize_site_folder(site_folder, file_name, website_zip_output)
            logger.info("ICC download complete.")
            return

        # ── Normal modes ────────────────────────────────────────────
        embed_images = not zip_output or both_output
        need_download = zip_output or both_output
        output_mode_str = (
            "both" if both_output else ("zip" if zip_output else "embed")
        )
        site_folder_local = ""  # only set in ICC mode above

        # Detect an offline viewer before image processing. Embed-only mode normally
        # does not write image files, but offline viewers need images/audio on disk.
        _viewer_meta_normal = None
        try:
            _viewer_meta_normal = get_viewer_for_site(viewer_html or "", mode=output_mode_str)
            if _viewer_meta_normal and not need_download:
                need_download = True
                logger.info("Offline viewer detected: enabling disk asset download for playable viewer output.")
        except Exception as _vm_e:
            logger.debug(f"Offline viewer pre-check skipped: {_vm_e}")

        tmp = None
        if need_download:
            tmp = create_random_temp_folder()

        working = cleaned
        if download_fonts and need_download and tmp:
            # In zip/embed mode, also scan viewer HTML for fonts
            working = _download_fonts_into_folder(
                working, base_url, tmp, html_source=viewer_html
            )

        embed_result, dl_result, _pi_urls = process_images(
            working, base_url,
            embed=embed_images, download=need_download,
            temp_folder=tmp, wait_time=wait_time, max_workers=max_workers,
            output_dir=output_dir, source_url=url,
            site_folder=site_folder_local,
        )

        if embed_images or both_output:
            has_edits_embed = (embed_result != cleaned)
            # Warn if output file will be very large (base64 inflates ~33%)
            size_mb = len(embed_result.encode("utf-8")) / (1024 * 1024)
            if size_mb > 50:
                logger.warning(
                    f"Output file besar: {size_mb:.0f} MB ({file_name}.json). "
                    f"This file may be slow to open in a browser. "
                    f"Consider ZIP mode for projects with many images."
                )
            if has_edits_embed:
                save_string_to_file(cleaned, file_name + "_original.json")
                logger.info(f"Saved: {file_name}_original.json (raw URLs)")
            save_string_to_file(embed_result, file_name + ".json")
            logger.info(f"Saved: {file_name}.json ({size_mb:.1f} MB, {'localized' if has_edits_embed else 'no URL changes'})")

            # ── Copy audio/ from temp to output_dir ───────────────────────
            # dl_result has "audio/ID.mp3" paths → audio files must be
            # alongside the .json so the viewer can load them.
            if tmp:
                _tmp_audio = os.path.join(tmp, "audio")
                if os.path.isdir(_tmp_audio):
                    _out_audio = os.path.join(output_dir or os.getcwd(), "audio")
                    _n = _copytree_merge_safe(_tmp_audio, _out_audio, label="audio")
                    logger.info(f"Saved/merged: audio/ ({_n} file(s))")

            # ── CYOA Manager integration ───────────────────────────────────
            # Only runs if user has enabled "→ CYOA Mgr" checkbox
            if cyoa_mgr_enabled:
                _cm_json_path = os.path.join(output_dir or os.getcwd(), file_name + ".json")
                _s  = _load_settings()
                _custom_db = _s.get("cyoa_mgr_db_path", "").strip()
                _cm_db = (_custom_db if _custom_db and os.path.exists(_custom_db)
                          else _find_cyoa_manager_db())
                if _cm_db:
                    add_to_cyoa_manager(
                        project_json_path=_cm_json_path,
                        name=file_name,
                        source_url=url,
                        viewer_preference=_cyoa_manager_viewer_pref(output_mode_str),
                        db_path=_cm_db,
                    )
        if both_output or not embed_images:
            has_edits_zip = (dl_result != cleaned)
            if has_edits_zip:
                save_string_to_file(cleaned, "project_original.json", tmp)
            save_string_to_file(dl_result, "project.json", tmp)
            logger.info(f"Saving: {file_name}.zip ({'with project_original.json' if has_edits_zip else 'no URL changes'})")
            zip_temp_folder(tmp, zip_name=file_name + ".zip")
            # Keep tmp until after offline viewer injection; it contains images/audio.

        # ── Feature 4: Save metadata.json ─────────────────────────────────
        try:
            meta_path = file_name + "_metadata.json"
            with open(meta_path, "w", encoding="utf-8") as _mf:
                json.dump(_metadata, _mf, indent=2, ensure_ascii=False)
            logger.info(f"Saved: {meta_path} (metadata)")
        except Exception as _me:
            logger.warning(f"Could not save metadata: {_me}")

        # ── Offline Viewer: apply registered viewer if available ────────────
        try:
            _viewer_meta = _viewer_meta_normal
            if not _viewer_meta:
                _page_html = ""
                _rp = None
                try:
                    _rp = fetch_response(url, timeout=8, extra_headers={"User-Agent": "Mozilla/5.0"})
                    if _rp is not None:
                        _page_html = _safe_response_text(_rp)
                except Exception as e:
                    logger.debug(f"Offline viewer page fetch skipped: {e}")
                finally:
                    if _rp is not None:
                        try:
                            _rp.close()
                        except Exception:
                            pass
                _viewer_meta = get_viewer_for_site(_page_html, mode=output_mode_str)
            if _viewer_meta:
                # Pass temp image/audio folders directly into the injected viewer.
                # Do not copy them to output_dir roots; that can delete/overwrite folders
                # from other projects.
                _offline_asset_sources: Dict[str, str] = {}
                if tmp and os.path.isdir(tmp):
                    for _asset_dir_name in ("images", "audio"):
                        _src = os.path.join(tmp, _asset_dir_name)
                        if os.path.isdir(_src):
                            _offline_asset_sources[_asset_dir_name] = _src
                # Always use dl_result (URLs kept as-is) for offline viewer injection.
                # embed_result has images as base64 → injecting it into app.js would
                # make the file hundreds of MB. The viewer loads images from the
                # images/ folder; we do NOT need base64 for the offline viewer.
                _viewer_out = _apply_offline_viewer(
                    output_dir=output_dir,
                    project_json_str=dl_result,
                    viewer_meta=_viewer_meta,
                    file_name=file_name,
                    asset_source_dirs=_offline_asset_sources,
                )
                if _viewer_out:
                    logger.info(
                        f"Offline viewer: {_viewer_meta.get('name','')} → "
                        f"{os.path.relpath(_viewer_out, output_dir)}"
                    )
        except Exception as _ov_e:
            logger.debug(f"Offline viewer step skipped: {_ov_e}")

        # ── Feature 5: Post-download validation ────────────────────────────
        try:
            _out_path = file_name + ".json"
            if os.path.exists(_out_path):
                _out_size = os.path.getsize(_out_path)
                if _out_size < 10:
                    logger.error(f"Validation FAIL: {_out_path} — file terlalu kecil ({_out_size} bytes), kemungkinan corrupt")
                else:
                    with open(_out_path, encoding="utf-8", errors="ignore") as _vf:
                        _sample = _vf.read(256)
                    if not (_sample.lstrip().startswith("{") or _sample.lstrip().startswith("[")):
                        logger.error(f"Validation FAIL: {_out_path} — bukan JSON valid (bisa jadi HTML error page)")
                    else:
                        try:
                            with open(_out_path, encoding="utf-8", errors="ignore") as _vf2:
                                _out_text = _vf2.read()
                            _vobj = json.loads(_out_text)
                            # Count referenced images vs actual base64 in file
                            _ref_count  = _out_text.count('"image":"') + _out_text.count('"image": "')
                            _b64_count  = _out_text.count("data:image/")
                            _url_count  = _ref_count - _b64_count
                            logger.info(
                                f"Validation OK: {_out_path} — "
                                f"{_ref_count} image refs, "
                                f"{_b64_count} base64, "
                                f"{_url_count} URL remaining"
                            )
                            if _url_count > 0 and embed_images:
                                logger.warning(
                                    f"Validation WARN: {_url_count} gambar masih berupa URL (bukan base64) — "
                                    f"may have failed to download. Check failed_images.txt"
                                )
                        except json.JSONDecodeError:
                            logger.error(f"Validation FAIL: {_out_path} — JSON parse error")
        except Exception as _ve:
            logger.warning(f"Validation error (non-critical): {_ve}")

    finally:
        # Temp cleanup must run on the exception path too.
        # Previously delete_temp_folder(tmp) sat outside this finally, so any
        # failure between create_random_temp_folder() and that line leaked the
        # /tmp/cyoa_* working folder. Path 1 (ICC) already used try/finally; this
        # brings the zip/embed/both path to parity.
        try:
            if tmp and os.path.isdir(tmp):
                delete_temp_folder(tmp)
        except Exception as _ignored_tmp_exc:
            logger.debug("Ignored temp cleanup exception: %s", _ignored_tmp_exc)
        try:
            os.chdir(original_dir)
        finally:
            _RUN_DOWNLOAD_LOCK.release()

    logger.info("Download successful.")


# Make the complete import surface visible before requesting runtime.surface.
# This breaks the direct-import cycle: surface can safely import these names
# while this module is paused in _sync_legacy_globals().
run_download = _base_run_download
_LAST_PREVIEW_FOLDER = None
_l = _sync_legacy_globals()
_v462_resolve_pure_download_url = getattr(_l, "_v462_resolve_pure_download_url")
_v462_run_download = getattr(_l, "_v462_run_download")
_v466_run_download = getattr(_l, "_v466_run_download")
run_download = getattr(_l, "run_download", _base_run_download)
_RUN_DOWNLOAD_LOCK = getattr(_l, "_RUN_DOWNLOAD_LOCK")
_LAST_PREVIEW_FOLDER = getattr(_l, "_LAST_PREVIEW_FOLDER", None)

__all__ = [
    "run_download", "_base_run_download", "_v462_resolve_pure_download_url",
    "_v462_run_download", "_v466_run_download", "_RUN_DOWNLOAD_LOCK",
    "_LAST_PREVIEW_FOLDER",
]
