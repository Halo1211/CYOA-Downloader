"""Additive policies for archiving JavaScript-driven websites."""

from __future__ import annotations

from dataclasses import dataclass


ARCHIVE_STRATEGIES = ("classic", "smart", "browser", "auto")
ARCHIVE_INTERACTION_POLICIES = ("off", "safe")


def _bounded_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(minimum, min(maximum, parsed))


@dataclass(frozen=True)
class ArchivePolicy:
    """Controls optional work beyond the historical single-page mirror."""

    strategy: str = "classic"
    max_pages: int = 300
    max_depth: int = 30
    capture_interactions: bool = False
    interaction_policy: str = "safe"
    settle_time_ms: int = 1800
    runtime_max_pages: int = 12
    max_scroll_steps: int = 100
    max_interactions: int = 20
    no_progress_rounds: int = 2

    def normalized(self) -> "ArchivePolicy":
        strategy = str(self.strategy or "classic").strip().lower()
        if strategy not in ARCHIVE_STRATEGIES:
            strategy = "classic"
        interaction_policy = str(self.interaction_policy or "off").strip().lower()
        if interaction_policy not in ARCHIVE_INTERACTION_POLICIES:
            interaction_policy = "safe"
        if self.capture_interactions:
            interaction_policy = "safe"
        return ArchivePolicy(
            strategy=strategy,
            max_pages=_bounded_int(self.max_pages, 300, 1, 5000),
            max_depth=_bounded_int(self.max_depth, 30, 0, 100),
            capture_interactions=bool(self.capture_interactions),
            interaction_policy=interaction_policy,
            settle_time_ms=_bounded_int(self.settle_time_ms, 1800, 250, 15000),
            runtime_max_pages=_bounded_int(self.runtime_max_pages, 12, 1, 100),
            max_scroll_steps=_bounded_int(self.max_scroll_steps, 100, 1, 1000),
            max_interactions=_bounded_int(self.max_interactions, 20, 0, 100),
            no_progress_rounds=_bounded_int(self.no_progress_rounds, 2, 1, 10),
        )

    @property
    def crawl_routes(self) -> bool:
        return self.strategy in {"smart", "browser"}

    @property
    def capture_runtime(self) -> bool:
        return self.strategy == "browser"

    @property
    def safe_interactions(self) -> bool:
        return self.interaction_policy == "safe" and self.max_interactions > 0
