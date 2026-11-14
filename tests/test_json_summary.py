"""Bounded string summaries validate full JSON and preserve image counts."""

import io
import json
import random
import threading

import pytest

from cyoa_downloader_app.core import cancellation
from cyoa_downloader_app.core.json_summary import load_json_summary
from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.download.orchestrator import _classify_project_image_references


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, 64, 65536])
def test_summary_matches_reference_counts_across_all_escape_boundaries(chunk_size):
    data = {"rows": [
        {"image": " " * 200 + "data:image/png;base64," + "X" * 8193},
        {"IMAGE": "\u3000https://example.test/α.png"},
        {"image": "\t \n", "nested": [{"image": "./local.png"}, {"image": "//cdn.example.test/x"}]},
        {"quote\\\"": "\b\f\r\n\t/\\\"😀雪", "image": 1},
    ], "version": "1" * 300 + ".2", "styling": {}}
    text = json.dumps(data, ensure_ascii=True)
    summary = load_json_summary(io.StringIO(text), chunk_size=chunk_size, preserve_keys={"version"})
    assert summary["version"] == data["version"]
    assert len(summary["rows"][0]["image"]) == 64
    assert _classify_project_image_references(summary) == _classify_project_image_references(data)


@pytest.mark.parametrize("text", [
    "", "{", "[1,]", '{"a":}', '{"a" 1}', '{"a":1 "b":2}', '{"a":1,}',
    "{}x", "true false", "[01]", "[.1]", "[1.]", "[1e]", "[+1]", "[True]",
    '["unterminated]', '["raw\ncontrol"]', '["bad\\x"]', '["bad\\u12"]', '["bad\\uZZZZ"]',
    '{"a"["v"]}', '{"image":"x" "image":"y"}', '["a""b"]', '["end\\', "[{]}",
])
def test_invalid_json_is_not_hidden_by_shortening(text):
    with pytest.raises(ValueError):
        json.loads(text)
    with pytest.raises(ValueError):
        load_json_summary(io.StringIO(text), chunk_size=3)


def test_duplicate_keys_escaped_names_and_long_keys_match_python_semantics():
    text = '{"image":"https://old/","im\\u0061ge":"data:image/png;base64,eA==",'
    text += '"' + "x" * 1000 + '":{"image":"https://one/"},'
    text += '"' + "x" * 999 + 'y":{"image":"https://two/"}}'
    assert _classify_project_image_references(load_json_summary(io.StringIO(text))) == \
        _classify_project_image_references(json.loads(text))


def test_generated_nested_json_has_identical_counts():
    rng = random.Random(42)
    values = ["", "  ", "a.png", "https://host/x", "//host/x", "data:image/png;base64,abc", None, 4]
    for _ in range(200):
        data = {"rows": [{"image": rng.choice(values), "x": [rng.random(), True, None, {"image": rng.choice(values)}]}
                         for _ in range(rng.randrange(1, 8))]}
        text = json.dumps(data, ensure_ascii=rng.choice([True, False]), indent=rng.choice([None, 2]))
        assert _classify_project_image_references(load_json_summary(io.StringIO(text), chunk_size=7)) == \
            _classify_project_image_references(data)


def test_preserves_python_decoder_non_finite_number_compatibility():
    data = load_json_summary(io.StringIO('[NaN, Infinity, -Infinity, {"image":"x"}]'))
    assert len(data) == 4 and data[-1]["image"] == "x"


def test_summary_only_makes_bounded_reads_and_can_cancel():
    event = threading.Event()
    class Source(io.StringIO):
        def read(self, size=-1):
            assert 0 < size <= 64
            event.set()
            return super().read(size)
    cancellation.set_progress_event_sink(None, event)
    try:
        with pytest.raises(DownloadCancelledError):
            load_json_summary(Source('{"image":"' + "x" * 1000 + '"}'), chunk_size=64)
    finally:
        cancellation.clear_progress_event_sink()
