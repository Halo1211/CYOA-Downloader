"""Generated-image inference must keep metadata rather than joined bundles."""

import tracemalloc

import pytest

from cyoa_downloader_app.download.asset_scan import _infer_generated_entry_images


def test_inference_does_not_allocate_a_copy_of_all_bundle_text():
    text = "/*" + "ordinary padding " * 65536 + "*/"
    sources = {f"bundle{i}.js": text for i in range(12)}
    tracemalloc.start()
    try:
        assert _infer_generated_entry_images(sources) == set()
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 * 1024, f"inference retained/joined bundle bodies: {peak} bytes"


def test_cross_file_contract_and_last_class_group_preserve_generated_names():
    sources = {
        "factory.js": '''const id = 'li_' + group + '_' + name.toLowerCase().replaceAll(' ', '');''',
        "view.js": "const path = `img/${item.id}.webp`;",
        "first.js": "class Thing extends Entry {\nconstructor(name) { super(name, 'old'); }\n}\nnew Thing('Old Name');",
        "second.js": "class Thing extends Entry {\nconstructor(name) { super(name, 'new'); }\n}\nnew Thing(\"A/B Name\");",
    }
    assert _infer_generated_entry_images(sources) == {
        "img/li_new_oldname.webp", "img/li_new_abname.webp",
    }


@pytest.mark.parametrize("name", ["THING", "thİng", "thıng"])
def test_constructor_matching_keeps_unicode_ignorecase_behavior(name):
    source = {
        "contract": """'li_' + group + '_' + name.toLowerCase().replaceAll(' ', ''); `img/${entry.id}.webp`""",
        "class": "class Thing extends Entry {\nsuper(name, 'group');\n}",
        "use": f"new {name}('Hero');",
    }
    assert _infer_generated_entry_images(source) == {"img/li_group_hero.webp"}


def test_distinct_identifiers_are_not_merged_by_full_casefold():
    source = {
        "contract": """'li_' + group + '_' + name.toLowerCase().replaceAll(' ', ''); `img/${entry.id}.webp`""",
        "classes": "class Straße extends Entry {\nsuper(name, 'sharp');\n}\nclass Strasse extends Entry {\nsuper(name, 'double');\n}",
        "use": "new Straße('One'); new STRASSE('Two');",
    }
    assert _infer_generated_entry_images(source) == {"img/li_sharp_one.webp", "img/li_double_two.webp"}


def test_compatibility_helper_keeps_fragments_split_across_sources():
    sources = {
        "first": "'li_'",
        "second": "+ group + '_' + name.toLowerCase().replaceAll(' ', '');",
        "template": "'img/${entry.id}.webp'; class Thing extends Entry {",
        "class_end": "super(name, 'group');\n}\nnew Thing",
        "constructor": "('Name');",
    }
    assert _infer_generated_entry_images(sources) == {"img/li_group_name.webp"}


def test_constructor_search_keeps_nested_literal_source_matches():
    sources = {
        "contract": """'li_' + group + '_' + name.toLowerCase().replaceAll(' ', ''); 'img/${entry.id}.webp';""",
        "class": "class Thing extends Entry {\nsuper(name, 'group');\n}",
        "use": """new Other("new Thing('Name')");""",
    }
    assert _infer_generated_entry_images(sources) == {"img/li_group_name.webp"}
