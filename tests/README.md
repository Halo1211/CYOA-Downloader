# Regression tests

Small, self-contained tests that lock the bug fixes and features from the rev18–rev23
stabilization series. Run them from the repository root or from this folder.

```bash
python tests/test_rev18.py   # batch mode-flag parity (GUI/CLI single source of truth)
python tests/test_rev19.py   # image-cache index load locking
python tests/test_rev22.py   # RAR handle leak fix + decode robustness
python tests/test_rev23.py   # queue preserved on unparseable status
python tests/test_rev20.py   # widget-after-destroy guard (needs Tk + a display)
```

Notes:

- `test_rev20.py` opens a real Tk window, so it needs a display. In headless CI use Xvfb:
  ```bash
  xvfb-run -a python tests/test_rev20.py
  ```
- The other four are pure-Python and need no display.
- These complement the built-in offline suite: `python cyoa_downloader.py --self-test`
  (currently 37/37).
