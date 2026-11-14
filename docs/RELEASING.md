# Windows release process

Publish a versioned release only after its source and extracted Windows packages
pass their checks. Keep generated artifacts outside source control. The build
includes optional Python integrations; Chromium, FFmpeg, JavaScript runtimes,
and RAR helpers remain external dependencies.

## Prepare the source

1. Update `VERSION`, `cyoa_downloader_app/app_info.py`, the README version badge,
   security support table, bug-report placeholder, and version assertions.
2. Move completed changelog entries under the release version and date. Keep
   public Markdown in English, use relative documentation links, and exclude
   machine-specific paths, credentials, and raw audit output.
3. Run compile checks, CLI help, dependency checks, offline self-tests, pytest,
   and Ruff as described in [CONTRIBUTING.md](../CONTRIBUTING.md). Check local
   Markdown links and review the staged diff for generated files or secrets.

## Build both layouts

Run from a Windows checkout with the required dependencies installed:

```powershell
.\tools\build_windows.ps1 -SkipInstall
.\tools\build_windows.ps1 -SkipInstall -Layout onedir
```

Each command runs checks by default. Use `-SkipChecks` only when the exact source
revision has already passed those checks. The default standalone layout produces
`dist/CYOA Downloader.exe` and `dist/CYOA-Downloader-Windows-x64.zip`.
The portable layout produces `dist/portable/CYOA Downloader/` and
`dist/portable/CYOA-Downloader-Windows-x64-portable.zip`.

Keep `_internal` next to the portable executable. Publish the entire portable
ZIP, not just that layout's EXE. The standalone EXE carries its runtime and
extracts it at launch; optional external tools are still required for their
features. Neither layout is code-signed.

## Validate the distributable

Extract each ZIP into a fresh directory and check ZIP CRC and expected entries.
Verify Tcl/Tk data/DLLs in the portable runtime, compare frozen application
modules with the source revision, and record file sizes and SHA-256 hashes.
Run the packaged executable checks against the extracted copy:

```powershell
python tools/smoke_windows_build.py "path/to/CYOA Downloader.exe" --require-self-test "history response header reuse" --report smoke.json
python tools/smoke_browser_reuse.py --executable "path/to/CYOA Downloader.exe" --report browser.json
python tools/smoke_runtime_waits.py --executable "path/to/CYOA Downloader.exe" --report runtime.json
```

The general smoke covers diagnostics and all nine output modes, ZIP integrity,
embedded images, transient HTTP 503 recovery, routes, iframe/XHTML/SVG/PDF,
and redirected CSS dependencies. Browser/runtime fixtures need the matching
installed Playwright Chromium. Keep profiles and output isolated from user data.
These CLI/output checks do not replace native GUI interaction checks.

## Publish

Commit the reviewed source, push without force, and tag the exact tested commit
as `vX.Y.Z`. Create a draft GitHub release with English notes and upload:

- `CYOA-Downloader-Windows-x64.exe` — standalone executable.
- `CYOA-Downloader-Windows-x64.zip` — standalone executable in a ZIP.
- `CYOA-Downloader-Windows-x64-portable.zip` — complete portable folder.
- `SHA256SUMS.txt` — hashes for those three artifacts.

Verify uploaded sizes and SHA-256 digests, then publish the draft. Confirm the
tag/commit, public asset links, and latest-release endpoint. Keep the previous
release for rollback. Do not upload local settings, logs, user downloads,
extractor scratch, or credentials. GitHub Actions also provides a Windows build
artifact for tag pushes; a successful local build does not imply hosted CI passed.
