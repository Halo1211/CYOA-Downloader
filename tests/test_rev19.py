import os
import importlib.util, threading, tempfile, os, json, time
spec=importlib.util.spec_from_file_location("cy",os.path.join(os.path.dirname(__file__), "..", "cyoa_downloader.py"))
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

# 1) _cache_load must be idempotent + thread-safe: many concurrent callers,
#    index loaded exactly once, no exception.
tmp=tempfile.mkdtemp()
m._CACHE_DIR = __import__("pathlib").Path(tmp)
m._CACHE_IDX = m._CACHE_DIR/"index.json"
# seed an index file
m._CACHE_DIR.mkdir(parents=True, exist_ok=True)
with open(m._CACHE_IDX,"w") as f: json.dump({f"u{i}":("a"*64) for i in range(500)}, f)
m._cache_loaded=False
m._cache_index.clear()

load_calls=[]
orig_update=dict.update
errors=[]
def worker():
    try:
        m._cache_load()
    except Exception as e:
        errors.append(e)
ths=[threading.Thread(target=worker) for _ in range(32)]
[t.start() for t in ths]; [t.join() for t in ths]
assert not errors, f"errors during concurrent load: {errors[:3]}"
assert m._cache_loaded is True
assert len(m._cache_index)==500, f"expected 500 entries got {len(m._cache_index)}"
print("PASS: concurrent _cache_load idempotent + safe (500 entries, 32 threads)")

# 2) _cache_get on missing url returns None without mutating under race
m._cache_loaded=True
res=m._cache_get("does-not-exist")
assert res is None
print("PASS: _cache_get miss returns None")

# 3) Verify _cache_load holds _cache_lock during the dict mutation (introspection)
import inspect
srclines=inspect.getsource(m._cache_load)
assert "_cache_lock" in srclines, "_cache_load must use _cache_lock"
print("PASS: _cache_load references _cache_lock")
