import os
import importlib.util, os
os.environ.setdefault("DISPLAY", ":99")
spec=importlib.util.spec_from_file_location("cy",os.path.join(os.path.dirname(__file__), "..", "cyoa_downloader.py"))
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

import tkinter as tk

# Helper must exist
assert hasattr(m, "_v25_safe_after_widget"), "missing _v25_safe_after_widget"

root = tk.Tk(); root.withdraw()

# 1) Normal case: callback runs while widget alive
lbl = tk.Label(root, text="x")
ran = {"n": 0}
m._v25_safe_after_widget(root, lbl, lambda: ran.__setitem__("n", ran["n"]+1))
root.update(); root.update_idletasks()
assert ran["n"] == 1, f"callback should run once, got {ran['n']}"
print("PASS: callback runs while widget alive")

# 2) TOCTOU: schedule, destroy widget BEFORE loop runs it -> no exception, fn skipped
lbl2 = tk.Label(root, text="y")
crashed = {"v": False}
def touch_dead():
    lbl2.configure(text="boom")   # would raise TclError on dead widget
m._v25_safe_after_widget(root, lbl2, touch_dead)
lbl2.destroy()                    # widget dead before Tk processes the after
try:
    root.update(); root.update_idletasks()
except Exception as e:
    crashed["v"] = True
    print("FAIL: exception escaped:", e)
assert not crashed["v"], "exception must not escape"
print("PASS: destroyed-widget callback skipped without raising")

# 3) Dead root: schedule on a destroyed root -> no-op, no raise
r2 = tk.Toplevel(root); w = tk.Label(r2)
r2.destroy()
m._v25_safe_after_widget(r2, w, lambda: w.configure(text="z"))  # must not raise
print("PASS: dead-root schedule is a safe no-op")

root.destroy()
print("ALL rev20 after-guard tests passed")
