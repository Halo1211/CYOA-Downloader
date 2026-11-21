"""One-item Auto lookahead owned by a single queue run."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError

from ..core.cancellation import _raise_if_cancelled
from ..core.progress import DownloadCancelledError
from ..logging_setup import logger
from ..project.discover import AutoDetection

# Detectors are replaceable plugin boundaries, as in auto_detect_modes_batch.
_DETECTOR_ERRORS = (Exception,)


class AutoModePreparation:
    """Keep at most one probe alive; only the queue worker mutates item state.

    The current item is resolved first, then the immediate next item may be
    prepared during its download. Results are indexed by snapshot position,
    never by URL, so duplicate rows retain separate identities. Shutdown joins
    the worker before the GUI can clear cancellation state or start a new run.
    """

    def __init__(self, items, detector, should_probe):
        self._items = items
        self._detector = detector
        self._should_probe = should_probe
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cyoa-auto")
        self._pending = None
        self.current_detection = None

    def prepare(self, index):
        _raise_if_cancelled()
        if index >= len(self._items) or not self._should_probe(self._items[index]):
            return
        if self._pending is not None:
            if self._pending[0] != index:
                raise RuntimeError("Auto lookahead advanced before consuming its result")
            return
        self._pending = (index, self._executor.submit(self._detect, self._items[index]["url"]))

    def _detect(self, url):
        _raise_if_cancelled()
        try:
            return self._detector(url)
        except DownloadCancelledError:
            raise
        except _DETECTOR_ERRORS as exc:
            logger.warning("[Auto-detect] Error for %s: %s", url, exc)
            return "embed"

    def resolve(self, index):
        self.current_detection = None
        self.prepare(index)
        if self._pending is None:
            return self._items[index].get("mode")
        _, future = self._pending
        while True:
            _raise_if_cancelled()
            try:
                result = future.result(timeout=0.1)
                break
            except TimeoutError:
                if future.done():
                    # A detector-raised TimeoutError is not a polling timeout.
                    result = future.result()
                    break
        _raise_if_cancelled()
        self._pending = None
        if isinstance(result, AutoDetection):
            self.current_detection = result
            mode = result.mode
        else:
            mode = result
        self._items[index].update(mode=mode, auto_detected=True)
        return mode

    def close(self):
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._pending = None
        self.current_detection = None
