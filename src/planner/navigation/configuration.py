"""Independent, tunable delivery budgets. No navigation semantics live here."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveryConfig:
    poll_seconds: float = 1.0
    heartbeat_seconds: float = 15.0
    send_timeout_seconds: float = 10.0
    page_batch_count: int = 100
    page_bytes: int = 1024 * 1024
    frame_bytes: int = 512 * 1024
    cursor_bytes: int = 4096
    proof_request_bytes: int = 16 * 1024
    retained_batch_count: int = 10000
    retained_bytes: int = 64 * 1024 * 1024
    retention_seconds: float = 30.0

    def __post_init__(self) -> None:
        if any(value <= 0 for value in vars(self).values()):
            raise ValueError("delivery limits must be positive")
        if self.frame_bytes > self.page_bytes:
            raise ValueError("frame budget must fit inside page budget")
