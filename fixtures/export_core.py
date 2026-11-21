import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "old"))
from cyoa_downloader_app.core import paths, url_utils

P = ["", None, "hello world", "\u4f60\u597d/\U0001f600/e\u0301", "rtl/\u05e9\u05dc\u05d5\u05dd", "a%2Fb%3Fc%23d", "%ZZ/%", "%E2%82%AC", "%FF", "a?b#c", r"a\\b", "nul.png", "CON", "COM\u00b9", "LPT3.txt", "con .txt", "C:foo", "../x", ".//..", "\x00x\x1f\x7f", "x\x1c\x1d\x1e\x1f\u0085\u00a0\u3000y", "x"*200, "\u754c"*80, "name." + "x"*20]
paths_cases=[]
def addp(fn,args,kwargs=None):
    try: paths_cases.append({"fn":fn,"args":args,"kwargs":kwargs or {},"ok":getattr(paths,fn)(*args,**(kwargs or {}))})
    except Exception as e: paths_cases.append({"fn":fn,"args":args,"kwargs":kwargs or {},"err":type(e).__name__})
for v in P: addp("_safe_rel_path",[v])
for v in P: addp("_is_windows_reserved_basename",[v])
for v in P: addp("_truncate_path_segment",[v or ""])
for v in P: addp("_safe_archive_rel_path",[v],{"decode_url":True})
for v in ["%2e%2e/x", "a//b", "a/./b", "a/../b", " a", "a ", "a.", "a/b?c", "a:b", "a\\b", "NUL", "foo%00bar", "", None]:
    addp("_safe_archive_rel_path",[v],{"decode_url":False})

U=["",None,"https://example.com","HTTP://Example.COM:80/a/../b?x=1#f","example.com:8080/a","//example.com/x","localhost","127.0.0.1","[::1]:443/x","https://[2001:db8::1]:443/a","https://b\u00fccher.de/a","ftp://example.com","file:///tmp/x","relative/path","https://exa mple.com","https://x.com/%","https://x.com/a//b","https://x.com/a/./b/../c","https://x.com:bad/","https://x.com:65536/","https://user:pw@x.com./a","https://x.com:443/a;b?x#y","https://x.com/\u4f60\u597d","https://x.com/\\x85", "https://10.0.0.1"]
url_cases=[]
def addu(fn,args,kwargs=None):
    try: url_cases.append({"fn":fn,"args":args,"kwargs":kwargs or {},"ok":getattr(url_utils,fn)(*args,**(kwargs or {}))})
    except Exception as e: url_cases.append({"fn":fn,"args":args,"kwargs":kwargs or {},"err":type(e).__name__})
for v in U: addu("canonicalize_url",[v])
for v in U: addu("is_probable_url",[v])
for v in U: addu("_directory_base_url",[v])
for v in ["http://x.com/a","https://x.com/a?query","https://x.com/"+"a"*120,"plain text","https://x.com/a#fragment"]: addu("truncate_display_url",[v],{"max_length":20})
for a,b in [("https://Example.com","https://example.com:443/x"),("http://x.com","https://x.com"),("https://x.com:443","https://x.com"),("https://10.0.0.1","https://10.0.0.1:443"),("bad","https://x.com"),("https://[::1]","https://[::1]:443")]: addu("_same_origin",[a,b])
for value in ["foo.png","/foo.png","//cdn.example/x.png","https://cdn.example/a.png","data:image/png,x","https://","myfolder/a.png","a b.png","\u4f60\u597d.png","dist/x.png"]:
    for kind in ["images","audio"]: addu("_candidate_urls_for_cyoap_asset",["https://example.com/game/index.html",value,kind])
with tempfile.TemporaryDirectory(prefix="cyoa-fixtures-") as temp_root:
    local_root = str(Path(temp_root) / "out")
    for v in ["https://example.com", "https://example.com/", "https://example.com/a%2520b.png", "https://example.com/a?x=1", "https://example.com/\u4f60\u597d"]:
        addu("_cyoap_local_path", [local_root, v])
    def portable(value):
        if isinstance(value, str):
            return value.replace(local_root, "<ROOT>").replace("\\", "/")
        if isinstance(value, list):
            return [portable(item) for item in value]
        if isinstance(value, dict):
            return {key: portable(item) for key, item in value.items()}
        return value
    url_cases = [portable(case) for case in url_cases]
Path("fixtures/core/paths.json").write_text(json.dumps(paths_cases,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
Path("fixtures/core/url_utils.json").write_text(json.dumps(url_cases,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(f"paths: {len(paths_cases)}; url_utils: {len(url_cases)}")



