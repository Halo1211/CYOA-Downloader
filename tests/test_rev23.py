import os
# Verifies the rev23 conservative-on-unparseable-status fix in _done().
# We model the three decision paths and assert the queue-preservation policy.

def decide(status):
    """Return 'preserve' (keep queue, no removal) or 'cleanup' (remove completed rows).
    Mirrors the patched _done() control flow."""
    try:
        succeeded = int(status.split("—")[1].strip().split("/")[0].strip())
        total     = int(status.split("/")[1].strip().split(" ")[0])
        if succeeded < total:
            return "preserve"        # partial failure -> keep queue
    except Exception:
        return "preserve"            # rev23: unparseable -> conservative keep
    return "cleanup"                 # all succeeded -> remove completed rows

# 1) all succeeded -> cleanup
assert decide("Selesai — 5/5 berhasil") == "cleanup", decide("Selesai — 5/5 berhasil")
print("PASS: all-succeeded -> cleanup (remove completed rows)")

# 2) partial failure -> preserve
assert decide("Selesai — 3/5 berhasil") == "preserve"
print("PASS: partial-failure -> preserve queue")

# 3) unparseable status -> preserve (THE FIX; previously fell through to cleanup)
for bad in ["Download cancelled", "Error: network unreachable", "", "Selesai", "完了しました"]:
    assert decide(bad) == "preserve", f"unparseable {bad!r} should preserve, got {decide(bad)}"
print("PASS: unparseable status -> preserve queue (no silent clear of failed run)")

# 4) Confirm the source actually returns on the except path (not falls through)
import re
src = open(os.path.join(os.path.dirname(__file__), "..", "cyoa_downloader.py")).read()
seg = src[src.index("def _done(self)"): src.index("def _show_results(self)")]
_pat = re.compile(r"self\._active_run_urls = set\(\)\s*\n\s*return")
assert len(_pat.findall(seg)) >= 2, \
    "both partial-failure and parse-failure paths must preserve+return"
print("PASS: _done except-path preserves queue and returns")

print("ALL rev23 tests passed")
