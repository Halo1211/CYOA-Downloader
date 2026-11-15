"""Bundle pruning must preserve native helpers and optional runtime resources."""

import pytest

from tools.bundle_policy import keep_bundle_member


@pytest.mark.parametrize("name", ["pandas/tests/io/data/excel/test1.xls", "pandas/tests/__init__.py", "pandas/conftest.py",
                                  r"pandas\tests\test_frame.py"])
def test_identified_development_tests_are_omitted(name):
    assert not keep_bundle_member(name, "win32")


@pytest.mark.parametrize("name", [
    "pandas/_testing/__init__.py", "pandas/testing.py", "pandas/_libs/testing.cp312-win_amd64.pyd",
    "pandas/io/excel/_xlrd.py", "numpy.libs/native.dll", "playwright/driver/node.exe",
    "playwright/driver/package/lib/server/chromium/chromium.js", "yt_dlp_ejs/yt/solver/core.js",
    "gallery_dl/extractor/twitter.py", "customtkinter/assets/themes/blue.json", "_tk_data/tk.tcl",
    "pandas-2.3.0.dist-info/licenses/LICENSE", "another/tests/runtime-required.dat",
])
def test_runtime_resources_and_public_helpers_are_preserved(name):
    assert keep_bundle_member(name, "win32")


@pytest.mark.parametrize("platform,native", [("win32", "windows"), ("darwin", "macos"), ("linux", "linux")])
def test_only_native_selenium_manager_is_selected(platform, native):
    for candidate in ("windows", "macos", "linux"):
        assert keep_bundle_member(f"selenium/webdriver/common/{candidate}/selenium-manager", platform) == (candidate == native)


def test_unknown_platform_and_normal_selenium_modules_are_preserved():
    assert keep_bundle_member("selenium/webdriver/common/linux/selenium-manager", "unknown")
    assert keep_bundle_member("selenium/webdriver/common/selenium_manager.py", "win32")
    assert keep_bundle_member("selenium/webdriver/firefox/webdriver.py", "win32")
