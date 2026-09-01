# CYOA Downloader Refactor — Current Architecture Notes

Dokumentasi pengguna utama berada di [README.md](README.md). Panduan arsitektur
arsip JavaScript berada di
[docs/JAVASCRIPT_ARCHIVE_GUIDE.md](docs/JAVASCRIPT_ARCHIVE_GUIDE.md).

This package retains the Phase 75 refactor and now also includes the additive
Classic/Smart/Browser JavaScript archive pipeline, GUI controls, route crawling,
runtime asset capture, readable settings metadata, and current regression fixes.

Fixed bug:

```text
NameError: name '_V466_PREVIOUS_SETUP_UI' is not defined
```

The bug came from splitting historical GUI monkey patches into separate modules:
late-captured patch globals needed one final namespace resync after all aliases
were imported.

Validation summary:

```text
pytest                         : 175 passed
compileall                     : OK
import package modules          : 108 imported / 0 failed
--help                         : OK
--dependency-check             : OK
--self-test                    : 37/37 passed
GUI instantiation smoke         : passed with CustomTkinter
original parity audit           : 0 missing / 5 expected additive signature diffs
legacy.py                       : deleted
```

Run:

```powershell
python cyoa_downloader.py
```

Recommended checks:

```powershell
python cyoa_downloader.py --help
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
```
