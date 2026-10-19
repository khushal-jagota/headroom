"""Pure wire validation: continuity identity must survive exact canonical encoding."""

import base64
import json

import pytest

from planner.navigation.contracts import Continuity
from planner.navigation.delivery_contracts import (
    ResetRequired,
    decode_cursor,
    encode_cursor,
)


def test_cursor_roundtrip_preserves_large_decimal_revision() -> None:
    value: Continuity = {
        "scope": "workspace",
        "schema_version": 6,
        "semantics": "attention-knowledge-v6-recap",
        "epoch": "epoch",
        "revision": "9007199254740993",
    }
    cursor = decode_cursor(encode_cursor(value))
    assert cursor["revision"] == "9007199254740993"
    assert cursor["wire_version"] == 2


@pytest.mark.parametrize("revision", ["00", "+1", "1.0", 1, True, "١"])
def test_cursor_rejects_noncanonical_revision(revision: object) -> None:
    raw = {
        "wire_version": 2,
        "scope": "s",
        "schema_version": 6,
        "semantics": "attention-knowledge-v6-recap",
        "epoch": "e",
        "revision": revision,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(raw).encode()).rstrip(b"=").decode()
    with pytest.raises(ResetRequired, match="invalid_cursor"):
        decode_cursor(encoded)


@pytest.mark.parametrize("value", ["", "!", "a" * 4097, "W10", "bnVsbA"])
def test_cursor_rejects_malformed_or_oversized_input(value: str) -> None:
    with pytest.raises(ResetRequired, match="invalid_cursor"):
        decode_cursor(value)
