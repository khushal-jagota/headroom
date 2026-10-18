import httpx
import pytest
from starlette.requests import Request

from planner.previews.api import (
    _forwarded_request_headers,
    _forwarded_response_headers,
    _rewrite_location,
    _upstream_url,
)


@pytest.mark.parametrize('location, expected', [
    ('/next?q=1#f', '/dev/tickets/t_example/8791/next?q=1#f'),
    ('http://localhost:8791/next', '/dev/tickets/t_example/8791/next'),
    ('//127.0.0.1:8791/next', '/dev/tickets/t_example/8791/next'),
    ('http://127.0.0.1:9999/next', 'http://127.0.0.1:9999/next'),
    ('https://example.com/next', 'https://example.com/next'),
])
def test_redirects_use_requested_port(location: str, expected: str) -> None:
    assert _rewrite_location(location, ticket_id='t_example', port=8791) == expected


def test_request_filters_credentials_and_preserves_duplicate_headers() -> None:
    raw = [(b'host', b'panels'), (b'authorization', b'secret'),
           (b'cookie', b'secret'), (b'x-plan-sprint-item-id', b'secret'),
           (b'x-plan-future-claim', b'secret'), (b'tailscale-user-name', b'secret'),
           (b'connection', b'x-secret'), (b'x-secret', b'secret'),
           (b'accept', b'a'), (b'accept', b'b')]
    request = Request({'type': 'http', 'headers': raw})
    assert _forwarded_request_headers(request) == [(b'accept', b'a'), (b'accept', b'b')]


def test_response_filters_origin_state_and_connection_headers() -> None:
    response = httpx.Response(302, headers=[
        ('set-cookie', 'secret'), ('clear-site-data', 'cookies'),
        ('www-authenticate', 'secret'), ('connection', 'x-secret'), ('x-secret', 'secret'),
        ('location', '/next'), ('x-test', 'one'), ('x-test', 'two'),
    ])
    headers = _forwarded_response_headers(response, ticket_id='t_example', port=8791)
    assert headers == [(b'location', b'/dev/tickets/t_example/8791/next'),
                       (b'x-test', b'one'), (b'x-test', b'two')]


def test_transport_url_preserves_encoded_path_and_query() -> None:
    request = Request({'type': 'http', 'scheme': 'http', 'server': ('panels', 80),
                       'path': '/dev/tickets/t_example/8791/a/b',
                       'raw_path': b'/dev/tickets/t_example/8791/a%2Fb',
                       'query_string': b'v=%2F&v=two', 'headers': []})
    url = _upstream_url(request, 42000)
    assert url.host == '127.0.0.1'
    assert url.port == 42000
    assert url.raw_path == b'/a%2Fb?v=%2F&v=two'
