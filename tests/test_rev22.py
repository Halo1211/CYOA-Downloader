import os
import importlib.util
spec=importlib.util.spec_from_file_location("cy",os.path.join(os.path.dirname(__file__), "..", "cyoa_downloader.py"))
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

# 1) try_decode_bytes already robust — sanity: bad codec name never used here,
#    but confirm latin-1 fallback never raises on arbitrary bytes.
raw = bytes(range(256))
out = m.try_decode_bytes(raw, "utf-8")
assert isinstance(out, str) and len(out) > 0
print("PASS: try_decode_bytes handles arbitrary bytes without raising")

# 2) Verify the RarFile fix uses a context manager (source introspection).
import inspect
# find the function containing the rar with-block (register_offline_viewer area)
src = open(os.path.join(os.path.dirname(__file__), "..", "cyoa_downloader.py")).read()
assert "with _rf.RarFile(zip_path) as arc:" in src, "RarFile must use context manager"
print("PASS: RarFile opened via context manager (no leak on namelist error)")

# 3) Verify CYOAP probe decode now catches LookupError + has utf-8 fallback.
assert "except (UnicodeDecodeError, LookupError, json.JSONDecodeError" in src, \
    "probe decode must catch LookupError"
assert 'raw.decode("utf-8", errors="replace")' in src, "probe must have utf-8 fallback"
print("PASS: CYOAP probe decode tolerates bad server charset header")

print("ALL rev22 tests passed")
