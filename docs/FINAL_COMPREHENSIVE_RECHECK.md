# Final Comprehensive Recheck — Phase 74

This is the final full audit after deleting `cyoa_downloader_app/legacy.py` and replacing it with the compatibility surface in `cyoa_downloader_app/runtime/surface.py`.

## Verdict

**No new refactor bug was found in this final comprehensive check.**

The refactored package still matches the original public callable surface/signatures and passes the full local test/CLI/import/compile audit. The original comparison target was:

```text
/mnt/data/cyoa_downloader(3).py
```

## Checks Performed

| Check | Result |
|---|---:|
| `PYTHONUTF8=1 python -m pytest -q` | `130 passed` |
| `python -m compileall -q cyoa_downloader.py cyoa_downloader_app tests tools` | OK |
| `python cyoa_downloader.py --help` | return code `0` |
| `python cyoa_downloader.py --dependency-check` | return code `0` |
| `python cyoa_downloader.py --self-test` | return code `0`, `37/37 passed` |
| Import all `cyoa_downloader_app` modules | `110 imported / 0 failed` |
| AST parse all Python files | `0 failures` |
| Original top-level callable names checked | `363` |
| Missing facade names vs original | `0` |
| Signature diffs vs original | `0` |
| Key constant diffs vs original | `0` |
| Behavior smoke mismatch vs original | `0 / 51` |
| Deleted `cyoa_downloader_app.legacy` import in package code | `0` |
| Physical `legacy.py` file exists | `False` |

## Help/CLI Parity

CLI flag parity is clean:

```json
{
  "original_flags": 79,
  "refactored_flags": 79,
  "missing_flags": [],
  "extra_flags": [],
  "only_usage_program_name_or_wrapping_diff": true
}
```

The only visible help diff is expected: the original uploaded file prints the program name as `cyoa_downloader(3).py`, while the refactored facade prints `cyoa_downloader.py`. The actual option/flag set is identical.

## Remaining Non-Bug Cleanup Debt

The deleted legacy module is no longer imported. Some internal bridge helpers still use names like `legacy()` or `_legacy()` but they return `cyoa_downloader_app.runtime.surface`, not `cyoa_downloader_app.legacy`. This is naming debt, not a runtime dependency. Files with such bridge naming:

```text
cyoa_downloader_app/cli.py
cyoa_downloader_app/diagnostics/runtime.py
cyoa_downloader_app/download/_bridge.py
cyoa_downloader_app/download/audio_download.py
cyoa_downloader_app/download/image_pipeline.py
cyoa_downloader_app/download/orchestrator.py
cyoa_downloader_app/download/website.py
cyoa_downloader_app/gui/_bridge.py
cyoa_downloader_app/gui/panels/_bridge.py
cyoa_downloader_app/gui/patches.py
cyoa_downloader_app/integrations/_bridge.py
cyoa_downloader_app/network/_bridge.py
cyoa_downloader_app/network/cloudflare.py
cyoa_downloader_app/network/dns.py
cyoa_downloader_app/network/fetch.py
cyoa_downloader_app/network/fetch_base.py
cyoa_downloader_app/project/_bridge.py
cyoa_downloader_app/project/cyoa_cafe.py
cyoa_downloader_app/project/cyoap_vue.py
cyoa_downloader_app/project/discover.py
cyoa_downloader_app/runtime/compat.py
```

## Important Limitations

This final check covers static parity, import safety, CLI behavior, self-test behavior, and deterministic smoke behavior. It does **not** prove every live network CYOA site behaves identically, because that requires testing real URLs against changing external sites. For that, the next recommended validation is a small fixture/live URL matrix comparing output folder/ZIP/report hashes between original and refactored builds.

## Final Status

`legacy.py` is deleted and not needed for runtime. The compatibility role is now handled by:

```text
cyoa_downloader_app/runtime/surface.py
```

Runtime surface size:

```text
1110 lines / 47293 bytes
```
