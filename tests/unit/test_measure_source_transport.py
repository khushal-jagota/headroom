"""Named authentication uses standard HTTP header forms."""

from planner.credentials.contracts import BasicSecret, BearerSecret, HeaderSecret
from planner.measure_sources.logic.transport import authentication_headers


def test_named_authentication_headers() -> None:
    assert authentication_headers(BearerSecret("token")) == {"Authorization": "Bearer token"}
    assert authentication_headers(HeaderSecret("X-Key", "token")) == {"X-Key": "token"}
    assert authentication_headers(BasicSecret("key", "")) == {"Authorization": "Basic a2V5Og=="}
