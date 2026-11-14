"""Bound queued download work without reducing the configured parallelism."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Executor, wait
from typing import TypeVar

from .cancellation import _raise_if_cancelled

_Item = TypeVar("_Item")
_Result = TypeVar("_Result")


def iter_bounded_results(
    executor: Executor,
    function: Callable[[_Item], _Result],
    items: Iterable[_Item],
    *,
    max_pending: int,
    check_cancelled: Callable[[], None] = _raise_if_cancelled,
) -> Iterator[tuple[_Item, _Result]]:
    """Yield every item/result as it completes, with bounded outstanding futures.

    Polling also observes cancellation while network workers are blocked. The
    caller owns executor shutdown; queued work is cancelled on early exit.
    """
    limit = max(1, int(max_pending))
    source = iter(items)
    pending = {}
    exhausted = False
    try:
        while pending or not exhausted:
            check_cancelled()
            while not exhausted and len(pending) < limit:
                check_cancelled()
                try:
                    item = next(source)
                except StopIteration:
                    exhausted = True
                    break
                pending[executor.submit(function, item)] = item
            if not pending:
                break
            ready, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            check_cancelled()
            for future in ready:
                check_cancelled()
                item = pending.pop(future)
                yield item, future.result()
    finally:
        for future in pending:
            future.cancel()
