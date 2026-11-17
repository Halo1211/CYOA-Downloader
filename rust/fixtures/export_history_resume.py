"""Export Python history/resume reference outputs. Run from the repository root."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cyoa_downloader_app.storage import history, resume  # noqa: E402

FIXED = "2026-11-18T02:00:00+07:00"


class _FixedDT:
    @staticmethod
    def now():
        class _N:
            def astimezone(self):
                class _A:
                    def isoformat(self):
                        return FIXED
                return _A()
        return _N()


resume._dt = _FixedDT
history.datetime = _FixedDT

keys = []
for args in [("https://x.com/a", "a.json", "auto"), (" https://x.com/a ", " a.json ", " ZIP "), ("", "", ""),
             ("https://x.com/你好", "名前.json", "Both"), ("u", "f", "   "), ("u\x1c", "f ", "Auto")]:
    keys.append({"args": list(args), "ok": resume.resume_job_key(*args)})

resume_cases = []
with tempfile.TemporaryDirectory() as tmp:
    for name, completed, failed in [("basic", ["a", "b"], []), ("unicode", ["你好", "é"], ["x\"y"]), ("empty", [], [])]:
        out = Path(tmp) / name
        resume.save_resume_state(str(out), completed, failed)
        resume_cases.append({"name": name, "completed": completed, "failed": failed,
                             "bytes": (out / "download_state.json").read_text(encoding="utf-8")})

history_cases = []
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "download_history.json"
    history._HISTORY_FILE = str(path)
    calls = [("https://x.com/a", "a.json", "auto", True), ("https://x.com/b", "b é.zip", "zip", False),
             ("https://x.com/a", "a2.json", "both", True)]
    import uuid
    seq = iter(f"{i:032x}" for i in range(1, 100))
    uuid.uuid4 = lambda: type("U", (), {"hex": next(seq)})()
    for url, fname, mode, ok in calls:
        history._record_history(url, fname, mode, ok, background=True)
        history._metadata_queue.cancel_all = getattr(history._metadata_queue, "cancel_all", None)
    # Background metadata may merge later; read immediately after the synchronous write.
    history_cases.append({"calls": [list(c) for c in calls], "bytes": path.read_text(encoding="utf-8")})

out = Path("rust/fixtures/history_resume")
out.mkdir(parents=True, exist_ok=True)
(out / "cases.json").write_text(json.dumps({"fixed_time": FIXED, "job_keys": keys, "resume": resume_cases,
                                            "history": history_cases}, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
print(f"job_keys {len(keys)} resume {len(resume_cases)} history {len(history_cases)}")
