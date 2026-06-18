## Summary

Describe the problem, the change, and why this approach is safe. Keep the explanation practical enough for a maintainer to review without guessing the intent.

## Area touched

- [ ] CLI flags or argument parsing
- [ ] GUI layout/theme/logo
- [ ] Downloader/network behavior
- [ ] Asset scanner
- [ ] Archive/path handling
- [ ] Offline Viewer Center or Manual Inject
- [ ] Batch import
- [ ] Dependency check or self-test
- [ ] Documentation only

## Compatibility checklist

- [ ] Existing CLI flags remain available.
- [ ] Existing output folder/ZIP structure is preserved.
- [ ] Batch TXT/CSV/XLSX behavior is preserved.
- [ ] Manual Inject is preserved.
- [ ] Offline viewer helpers are preserved.
- [ ] Userscript serve helper behavior is preserved.
- [ ] No new mandatory dependency was added without justification.
- [ ] Documentation was updated if behavior changed.

## Tests run

```text
python -m py_compile cyoa_downloader.py
python -c "import ast, pathlib; ast.parse(pathlib.Path('cyoa_downloader.py').read_text(encoding='utf-8')); print('ast.parse OK')"
python cyoa_downloader.py --help
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
pytest -q
ruff check cyoa_downloader.py --select F821
```

## GUI evidence

For GUI changes, add screenshots for:

- System theme when possible;
- Dark theme;
- Light theme;
- relevant dialog or panel.

## Risk notes

List any behavior that still needs manual testing, especially live websites, Cloudflare-protected URLs, media extraction, or large offline viewer packages.
