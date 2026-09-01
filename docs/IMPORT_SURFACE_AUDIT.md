# Import Surface Audit

Module: `cyoa_downloader`
Required names: 15
Missing names: 0
- `main` → `cyoa_downloader_app.cli`
- `launch_gui` → `cyoa_downloader_app.gui.app`
- `run_download` → `cyoa_downloader_app.gui.final_behaviors`
- `CYOADownloaderGUI` → `cyoa_downloader_app.gui.app`
- `WebsiteDownloader` → `cyoa_downloader_app.download.website`
- `fetch_response` → `cyoa_downloader_app.network.fetch`
- `process_images` → `cyoa_downloader_app.download.image_pipeline`
- `get_project_source` → `cyoa_downloader_app.project.discover`
- `auto_detect_mode` → `cyoa_downloader_app.project.discover`
- `_derive_mode_flags` → `cyoa_downloader_app.importers.batch`
- `_cache_load` → `cyoa_downloader_app.storage.cache`
- `_cache_get` → `cyoa_downloader_app.storage.cache`
- `_v25_safe_after_widget` → `cyoa_downloader_app.gui.widgets`
- `try_decode_bytes` → `cyoa_downloader_app.project.parse`
- `userscript_integration_report` → `cyoa_downloader_app.preview_assets`
