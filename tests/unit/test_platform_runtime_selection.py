"""An unavailable OS must never receive Linux process or service operations."""

import pytest

from planner.platform_runtime.contracts import UnsupportedPlatformError
from planner.platform_runtime.selection import get_platform_adapter


def test_unsupported_platform_refuses_instead_of_using_linux() -> None:
    with pytest.raises(UnsupportedPlatformError, match="not implemented for darwin"):
        get_platform_adapter("darwin")
