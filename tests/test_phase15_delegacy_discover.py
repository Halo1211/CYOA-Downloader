import ast
from pathlib import Path

import cyoa_downloader
from cyoa_downloader_app.project import discover as discover_mod

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "cyoa_downloader_app" / "runtime" / "surface.py"
DISCOVER = ROOT / "cyoa_downloader_app" / "project" / "discover.py"


def _legacy_defined_symbols():
    tree = ast.parse(LEGACY.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def test_phase15_discovery_helpers_are_real_module_exports():
    assert cyoa_downloader.find_candidate_urls_in_text is discover_mod.find_candidate_urls_in_text
    assert cyoa_downloader.find_script_sources is discover_mod.find_script_sources
    assert cyoa_downloader.find_scripts is discover_mod.find_scripts
    assert cyoa_downloader.extract_placeholder_url is discover_mod.extract_placeholder_url
    assert cyoa_downloader.extract_iframe_urls is discover_mod.extract_iframe_urls
    assert cyoa_downloader.extract_app_js_path is discover_mod.extract_app_js_path
    assert cyoa_downloader.build_default_project_candidates is discover_mod.build_default_project_candidates
    assert cyoa_downloader.strip_document_from_url is discover_mod.strip_document_from_url


def test_phase15_low_risk_discovery_helpers_moved_out_of_legacy():
    names = _legacy_defined_symbols()
    for name in {
        "find_candidate_urls_in_text", "_script_priority", "find_script_sources",
        "_scan_html_for_project_hints", "find_scripts", "extract_placeholder_url",
        "extract_iframe_urls", "get_first_folder_from_url", "extract_app_js_path",
        "build_default_project_candidates", "strip_document_from_url",
    }:
        assert name not in names


def test_phase15_discovery_module_only_uses_lazy_legacy_bridge():
    source = DISCOVER.read_text(encoding="utf-8")
    before_lazy = source.split("def _legacy", 1)[0]
    assert "from .. import legacy" not in before_lazy
    assert "from ..runtime import surface" in source  # remaining delegates now target the compatibility surface


def test_phase15_find_candidate_urls_smoke():
    text = '''
      fetch("data/project.json");
      e.open("GET","assets/project.txt",!0);
      const escaped = "json\\/project.zip";
      const label = "Load/Save Project";
    '''
    candidates = discover_mod.find_candidate_urls_in_text(text, "https://example.test/game/index.html")
    assert "https://example.test/game/data/project.json" in candidates
    assert "https://example.test/game/assets/project.txt" in candidates
    assert "https://example.test/game/json/project.zip" in candidates
    assert not any("Load/Save" in c for c in candidates)


def test_phase15_html_and_default_candidate_smoke():
    html = '''
      <meta name="cyoa-project" content="project.json">
      <iframe src="https://viewer.example/app"></iframe>
      <script>window.__PROJECT__="data/project.txt";</script>
      <script>window.project={rows:[],pointTypes:[]}</script>
    '''
    assert discover_mod.extract_iframe_urls(html) == ["https://viewer.example/app"]
    assert discover_mod.find_scripts(html) == [
        'window.__PROJECT__="data/project.txt";',
        "window.project={rows:[],pointTypes:[]}",
    ]
    hints = discover_mod._scan_html_for_project_hints(
        html,
        "https://example.test/game/",
        "https://example.test/game/",
    )
    assert "https://example.test/game/project.json" in hints
    assert "https://example.test/game/data/project.txt" in hints

    assert discover_mod.strip_document_from_url("https://example.test/a/b/index.html?x=1") == "https://example.test/a/b/"
    defaults = discover_mod.build_default_project_candidates("https://example.test/a/b/index.html")
    assert defaults[0] == "https://example.test/a/b/project.json"
    assert "https://example.test/a/project.json" in defaults


def test_phase15_follows_runtime_app_bundle_from_bootstrap(monkeypatch):
    sources = {
        "https://example.test/game/js/core.js": (
            "add('script',{src: basePath + 'js/app.ABC123.js'});"
        ),
        "https://example.test/game/js/app.ABC123.js": "const app=i({rows:[]});",
    }

    monkeypatch.setattr(
        discover_mod,
        "_get_source",
        lambda url, extra_headers=None: sources.get(url),
    )
    html = '<script src="js/core.js"></script>'
    found = discover_mod.find_script_sources(html, "https://example.test/game/")
    labels = [label for label, _source in found]
    assert "https://example.test/game/js/core.js" in labels
    assert "https://example.test/game/js/app.ABC123.js" in labels
