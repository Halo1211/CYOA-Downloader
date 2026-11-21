"""Browser calls, page disposal and disk handoff belong to one job thread."""

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import requests

from cyoa_downloader_app.core import cancellation
from cyoa_downloader_app.core.media_io import FilePayload
from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.network import browser, browser_owner


def test_frozen_browser_uses_exact_external_revision_without_overriding_configuration(monkeypatch, tmp_path):
    monkeypatch.setattr(browser_owner.sys, "frozen", True, raising=False)
    monkeypatch.setattr(browser_owner.sys, "platform", "win32")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    relative = ("chromium-1217", "chrome-win64", "chrome.exe")
    expected = tmp_path / "bundle" / ".local-browsers"
    for part in relative:
        expected /= part
    api = SimpleNamespace(executable_path=str(expected))
    wrong_revision = tmp_path / "local" / "ms-playwright" / "chromium-9999" / "chrome-win64" / "chrome.exe"
    wrong_revision.parent.mkdir(parents=True)
    wrong_revision.touch()
    assert browser_owner._external_frozen_chromium(api) is None
    installed = tmp_path / "local" / "ms-playwright"
    for part in relative:
        installed /= part
    installed.parent.mkdir(parents=True)
    installed.touch()
    assert browser_owner._external_frozen_chromium(api) == str(installed)
    for configured in ("0", str(tmp_path / "custom"), ""):
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", configured)
        assert browser_owner._external_frozen_chromium(api) is None
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH")
    expected.parent.mkdir(parents=True)
    expected.touch()
    assert browser_owner._external_frozen_chromium(api) is None
    expected.unlink()
    monkeypatch.setattr(browser_owner.sys, "frozen", False)
    assert browser_owner._external_frozen_chromium(api) is None


def test_parallel_callers_share_one_owner_and_close_on_that_owner(monkeypatch):
    session = browser.BrowserFetchSession()
    threads, active, peak, finished = [], [0], [0], []
    def work(value):
        threads.append(threading.get_ident())
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        time.sleep(.005)
        active[0] -= 1
        return value
    monkeypatch.setattr(session, "_finish", lambda: finished.append(threading.get_ident()))
    with ThreadPoolExecutor(max_workers=6) as pool:
        result = list(pool.map(lambda n: session._call(work, n), range(20)))
    session.close()
    session.close()
    assert result == list(range(20)) and peak[0] == 1
    assert len(set(threads)) == 1 and finished == [threads[0]]
    assert threads[0] != threading.get_ident()
    assert session._call(work, 22) is None
    assert not any(t.ident == threads[0] and t.is_alive() for t in threading.enumerate())


def test_cancel_admission_and_active_wait_does_not_skip_owner_cleanup(monkeypatch):
    session = browser.BrowserFetchSession()
    entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
    cleanup = []
    def work():
        entered.set()
        release.wait(2)
        cancellation._raise_if_cancelled()
        return "late"
    monkeypatch.setattr(session, "_finish", lambda: cleanup.append(threading.get_ident()))
    cancellation.set_progress_event_sink(None, cancel)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(session._call, work)
            assert entered.wait(1)
            second = pool.submit(session._call, work)
            cancel.set()
            for future in (first, second):
                with pytest.raises(DownloadCancelledError):
                    future.result(1)
            release.set()
        session.close()
        assert cleanup == [session._owner_thread]
    finally:
        release.set()
        session.close()
        cancellation.clear_progress_event_sink()


def test_close_discards_late_result_and_blocks_new_admission(monkeypatch):
    session = browser.BrowserFetchSession()
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(session, "_finish", lambda: None)
    def work():
        entered.set()
        release.wait(2)
        return "late"
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(session._call, work)
        assert entered.wait(1)
        closing = pool.submit(session.close)
        try:
            deadline = time.monotonic() + 1
            while not session._closed and time.monotonic() < deadline:
                time.sleep(.002)
            assert session._closed
        finally:
            release.set()
        closing.result(2)
        assert pending.result(2) is None


def test_queued_fetch_rechecks_vpn_before_launch(monkeypatch):
    session = browser.BrowserFetchSession()
    entered, release = threading.Event(), threading.Event()
    allowed = [True]
    monkeypatch.setattr(browser, "vpn_requirement_satisfied", lambda: allowed[0])
    monkeypatch.setattr(session, "_ensure", lambda *a: pytest.fail("queued fetch bypassed VPN guard"))
    monkeypatch.setattr(session, "_finish", lambda: None)
    def blocked():
        entered.set()
        release.wait(2)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            active = pool.submit(session._call, blocked)
            assert entered.wait(1)
            queued = pool.submit(session.fetch, "https://one.test/")
            allowed[0] = False
            release.set()
            assert active.result(2) is None and queued.result(2) is None
    finally:
        release.set()
        session.close()


@pytest.fixture
def fake_playwright(monkeypatch):
    api = pytest.importorskip("playwright.sync_api")
    calls, contexts, launches, pages = [], [], [], []
    response = SimpleNamespace(ok=True, status=200, headers={"content-type": "image/png"},
                               url="https://one.test/image.png", body=lambda: b"\x89PNG" + b"x" * 100,
                               request=SimpleNamespace(timing={"responseEnd": 1}, failure=None), progress=lambda: None)
    def touch(label):
        calls.append((label, threading.get_ident()))
    class Page:
        def wait_for_timeout(self, milliseconds):
            touch("wait")
            time.sleep(.001)
            response.progress()
        def goto(self, url, **kwargs):
            touch("goto")
            assert kwargs["wait_until"] == "domcontentloaded"
            response.url = url
            return response
        def close(self):
            touch("page.close")
            self.closed = True
    class Context:
        def __init__(self):
            self.pages = []
        def add_cookies(self, cookies):
            touch("cookies")
            self.cookies = cookies
        def new_page(self):
            touch("page")
            result = Page()
            result.closed = False
            pages.append(result)
            self.pages.append(result)
            return result
        def close(self):
            touch("context.close")
            for page in self.pages:
                if not page.closed:
                    page.close()
            self.closed = True
    class Browser:
        def new_context(self, **kwargs):
            touch("context")
            assert kwargs["ignore_https_errors"] is False
            result = Context()
            result.closed, result.cookies, result.agent = False, [], kwargs["user_agent"]
            contexts.append(result)
            return result
        def close(self):
            touch("browser.close")
    def launch(**kwargs):
        touch("launch")
        launches.append(kwargs)
        return Browser()
    class Runtime:
        chromium = SimpleNamespace(launch=launch)
        def start(self):
            touch("start")
            return self
        def stop(self):
            touch("stop")
    monkeypatch.setattr(api, "sync_playwright", Runtime)
    monkeypatch.setattr(browser, "vpn_requirement_satisfied", lambda: True)
    proxy = [{}]
    monkeypatch.setattr(browser, "_get_browser_proxy_config", lambda url: proxy[0])
    http = [requests.Session()]
    http[0].headers["User-Agent"] = "Fixture agent"
    http[0].cookies.set("auth", "one", domain="one.test", path="/")
    monkeypatch.setattr(browser, "_get_shared_session", lambda *a: http[0])
    return SimpleNamespace(calls=calls, contexts=contexts, launches=launches, pages=pages,
                           response=response, http=http, proxy=proxy)


def test_origin_cookie_session_agent_and_proxy_changes_recreate_isolated_contexts(fake_playwright):
    fixture = fake_playwright
    session = browser.BrowserFetchSession()
    try:
        assert session.fetch("https://one.test/a") is not None
        assert session.fetch("https://one.test/b") is not None
        assert len(fixture.launches) == len(fixture.contexts) == 1
        assert fixture.contexts[0].cookies[0]["value"] == "one"
        session.fetch("https://two.test/a")
        assert fixture.contexts[0].closed and fixture.contexts[1].cookies == []
        fixture.http[0].headers["User-Agent"] = "Changed agent"
        session.fetch("https://two.test/a")
        assert fixture.contexts[-1].agent == "Changed agent"
        fixture.http[0] = requests.Session()
        session.fetch("https://two.test/a")
        assert len(fixture.contexts) == 4
        fixture.proxy[0] = {"server": "http://proxy.test:8080"}
        session.fetch("https://two.test/a")
        assert len(fixture.launches) == 2 and fixture.launches[-1]["proxy"] == fixture.proxy[0]
    finally:
        session.close()
    assert all(page.closed for page in fixture.pages)
    assert {thread for _, thread in fixture.calls} == {session._owner_thread}
    assert fixture.calls[-1][0] == "stop"


def test_disk_handoff_runs_after_page_disposal_and_failures_do_not_launch_selenium(fake_playwright, monkeypatch, tmp_path):
    session = browser.BrowserFetchSession(page_request_limit=1)
    def retain(body, directory):
        assert fake_playwright.pages[-1].closed
        raise OSError("fixture disk full")
    monkeypatch.setattr(session, "_retain", retain)
    monkeypatch.setattr(browser, "_fetch_selenium", lambda *a: pytest.fail("disk error retried through Selenium"))
    try:
        with pytest.raises(OSError, match="fixture disk full"):
            session.fetch_headless("https://one.test/a", spool_dir=str(tmp_path))
    finally:
        session.close()


def test_file_result_is_owned_by_session_and_disposed_on_close(fake_playwright):
    session = browser.BrowserFetchSession()
    try:
        result = session.fetch("https://one.test/a", file_backed=True)
        assert isinstance(result.content, FilePayload)
        from pathlib import Path
        path = Path(result.content.path)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == result.content.sha256
    finally:
        session.close()
    assert not path.exists()


@pytest.mark.parametrize("request_limit,byte_limit", [(2, 10000), (10, 200)])
def test_pages_rotate_at_request_or_body_budget_without_relaunch(fake_playwright, request_limit, byte_limit):
    session = browser.BrowserFetchSession(page_request_limit=request_limit, page_byte_limit=byte_limit)
    try:
        for i in range(3):
            assert session.fetch(f"https://one.test/{i}") is not None
        assert len(fake_playwright.launches) == 1
        assert len(fake_playwright.pages) == 2
        assert fake_playwright.pages[0].closed
        assert not fake_playwright.pages[-1].closed
    finally:
        session.close()


def test_body_deadline_is_enforced_before_requesting_full_body(fake_playwright):
    session = browser.BrowserFetchSession()
    fake_playwright.response.request.timing["responseEnd"] = -1
    fake_playwright.response.body = lambda: pytest.fail("Unfinished body was requested")
    try:
        assert session.fetch("https://one.test/a", timeout_ms=10) is None
        assert fake_playwright.pages[-1].closed
    finally:
        session.close()


def test_active_body_wait_observes_cancel_on_owner(fake_playwright):
    session = browser.BrowserFetchSession()
    cancel = threading.Event()
    fake_playwright.response.request.timing["responseEnd"] = -1
    fake_playwright.response.progress = cancel.set
    fake_playwright.response.body = lambda: pytest.fail("Cancelled body was requested")
    cancellation.set_progress_event_sink(None, cancel)
    try:
        with pytest.raises(DownloadCancelledError):
            session.fetch("https://one.test/a")
        session.close()
        assert fake_playwright.pages[-1].closed
    finally:
        session.close()
        cancellation.clear_progress_event_sink()


def test_explicit_zero_timeout_retains_playwright_unlimited_semantics(fake_playwright):
    session = browser.BrowserFetchSession()
    fake_playwright.response.request.timing["responseEnd"] = -1
    fake_playwright.response.progress = lambda: fake_playwright.response.request.timing.update(responseEnd=1)
    try:
        assert session.fetch("https://one.test/a", timeout_ms=0) is not None
    finally:
        session.close()
