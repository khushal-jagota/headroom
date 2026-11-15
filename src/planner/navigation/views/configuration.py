"""Budgets for bounded navigation reads and delivery."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ViewConfig:
    maximum_records: int = 512
    maximum_bytes: int = 768 * 1024
    maximum_windows: int = 160
    maximum_window_rows: int = 60
    maximum_requested_rows: int = 256
    inline_recap_bytes: int = 2048
    content_chunk_bytes: int = 64 * 1024
    retained_revisions: int = 10000
    locales: tuple[str, ...] = ("en-US", "de-DE")

    def __post_init__(self) -> None:
        if any(value <= 0 for value in vars(self).values() if isinstance(value, int)):
            raise ValueError("view budgets must be positive")
        if not self.locales or len(set(self.locales)) != len(self.locales):
            raise ValueError("view locales must be nonempty and unique")


DEFAULT_CONFIG = ViewConfig()
