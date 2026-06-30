## Summary

Briefly describe what this pull request changes and why.

## Type of change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Documentation only
- [ ] Refactor / maintenance

## Compatibility

- [ ] No change to the default download path or output layout
- [ ] No CLI flag renames or removals
- [ ] No internal mode-key changes (`website_zip` / `website_folder`)
- [ ] Documentation updated where user-facing behavior changed

## Testing

Confirm the gate suite passes locally:

- [ ] `python -m py_compile cyoa_downloader.py`
- [ ] `python cyoa_downloader.py --help`
- [ ] `python cyoa_downloader.py --dependency-check`
- [ ] `python cyoa_downloader.py --self-test` (state the count, e.g. 37/37)
- [ ] `ruff check cyoa_downloader.py --select F821,F811,F601`
- [ ] `pytest -q` (if tests are affected)

## Notes for the maintainer

Anything reviewers should pay special attention to, plus what was and was not live-tested.
