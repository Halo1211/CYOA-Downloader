# Website / ICC mode: port plan (audit by Luna, session 5; line numbers verified on demand)

## Python pipeline
1. `orchestrator._base_run_download` picks the branch (website_output / pure_website / custom-viewer fallback), lines 237–298, 354–424, 484–527 and 638–649.
2. `WebsiteDownloader.download()` → `_download_html` / `_localize_html` → `index.html` (website.py 573–579, 2739–2758).
3. HTML reference rewrite and dependency fetch: `_localize_html`, `_set_attr_local` (2588–2673, 2810–3043).
4. Asset mapping: `_kind_from` 1289–1310, `_allocate_local_path` 1356–1451, `_local_asset_reference` 1453–1482, URL helpers 1212–1287.
5. Text asset rewrite: `_process_css` 2427–2469, `_process_js` 2524–2572, `localize_existing_text_assets` 1069–1120.
6. `archive_runner.run_archive_extensions`: classic has no crawl and no manifest; smart/browser crawl routes and write `archive_manifest.json`.
7. ICC: `process_images(site_folder=…)`, then `project.json` / `project_original.json`, `backup_report.txt` and the integrity check (orchestrator 660–785).
8. `package._finalize_site_folder` (48–57) writes `<file_name>_site.zip` and removes the folder in ZIP mode.

## Rust batches
- **W1 (pure):** `_kind_from`, URL normalization, `_allocate_local_path`, `_local_asset_reference`. Parity via Python fixtures.
- **W2:** HTML/CSS/JS reference rewrite with stubbed fetches (golden fixtures).
- **W3:** classic static mirror over the policy-checked client (bounded graph, containment).
- **W4:** ICC integration (`process_images` site_folder), `backup_report.txt`, `_site.zip` finalize, CLI `--icc/--icc-folder`, batch modes `website_zip` / `website_folder`.
- **W5:** smart route crawl (same-origin, max pages 300 / depth 30, manifest). Browser runtime capture comes later (CDP, D3).

## Security
- Containment and symlink checks on every write.
- Route crawl caps.
- Cross-origin internal screening.
- At most 10 redirects, with credentials stripped cross-origin.
- `validate_integrity` traversal tests.
