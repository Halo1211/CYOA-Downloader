# Phase 75 — Runtime GUI Recheck and Fix

This phase fixes the GUI runtime bug reported after Phase 74:

```text
NameError: name '_V466_PREVIOUS_SETUP_UI' is not defined
```

## Root cause

The original single-file script used one shared global namespace for all GUI
behavior functions. After splitting the GUI behavior bodies into modules, each
moved function kept its own module globals. `_V466_PREVIOUS_SETUP_UI` was captured in
`gui/bootstrap.py`, but the value was inserted into the compatibility namespace
after the final GUI behavior module had already mirrored globals into its own
namespace.

This meant the static import/CLI/self-test audits passed, but a real GUI
instantiation could fail once `_v466_setup_ui()` executed.

## Fix

- Capture `_V466_PREVIOUS_SETUP_UI` before syncing final GUI behavior globals.
- Add `resync_gui_runtime(namespace)` to mirror the final compatibility surface
  into the moved GUI behavior module after final imports are complete.
- Call this final GUI resync from `runtime/surface.py` after network/project/
  download aliases are imported.
- Add regression tests that scan GUI behavior functions for unresolved cross-step
  globals after bootstrap.

## Validation

```text
PYTHONUTF8=1 python -m pytest -q                         -> 132 passed
python -m compileall -q cyoa_downloader_app cyoa_downloader.py -> OK
import all package modules                                -> 110 imported / 0 failed
python cyoa_downloader.py --help                         -> return code 0
python cyoa_downloader.py --dependency-check             -> return code 0
python cyoa_downloader.py --self-test                    -> 37/37 passed
xvfb GUI instantiate smoke via compat CYOADownloaderGUI   -> passed
xvfb python cyoa_downloader.py startup smoke              -> no traceback before timeout
original parity audit against cyoa_downloader(3).py       -> 0 missing / 0 signature diff / 0 constant diff
```

## Notes

`legacy.py` remains deleted. The fix only updates GUI behavior bootstrap/resync
logic and does not change CLI flags, public call signatures, constants, or output
format contracts.
