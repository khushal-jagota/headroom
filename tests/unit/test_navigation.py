"""Navigation contract validation without a runtime or providers."""

import pytest

from planner.navigation.contracts import parse_revision


@pytest.mark.parametrize("value", ["", "00", "01", "+1", "-1", " 1", "1.0", "١"])
def test_revision_rejects_noncanonical_decimal(value: str) -> None:
    with pytest.raises(ValueError):
        parse_revision(value)


def test_revision_preserves_large_integer_precision() -> None:
    assert parse_revision("9007199254740993") == 9007199254740993
