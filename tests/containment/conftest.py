"""Require admitted containment before any explicitly requested probe imports."""

import pytest
from test_containment.admission import requested_containment
from test_containment.contracts import ContainmentRefusal

try:
    if requested_containment() is None:
        raise ContainmentRefusal("containment probes require explicit OS boundary admission")
except ContainmentRefusal as error:
    raise pytest.UsageError(
        f"Test containment refused: {error}. Use scripts/run_contained_pytest.py."
    ) from error
