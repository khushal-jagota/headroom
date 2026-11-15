"""Skip a test that needs Worker cgroup custody on a host without delegated cgroup v2."""

from __future__ import annotations

from uuid import uuid4

import pytest


def require_delegated_cgroup() -> None:
    from planner.platform_runtime.linux import worker_cgroups

    try:
        identity = worker_cgroups.create(uuid4().hex)
        worker_cgroups.remove(identity)
    except (OSError, ValueError, StopIteration) as error:
        pytest.skip(f"Delegated cgroup v2 is required: {error}")
