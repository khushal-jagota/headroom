"""Wall-clock timestamps shared by the Mac and VPS executor peers."""

import time


def now_ms() -> int:
    return time.time_ns() // 1_000_000
