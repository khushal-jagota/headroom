"""Share the real-server fixture without classifying process tests as browser E2E.

The fixtures are imported rather than reached through ``pytest_plugins``: pytest no
longer accepts that name outside the rootdir conftest, and declaring it here made a
plain ``pytest`` run fail to collect this directory at all. Importing the fixture
functions into this conftest scopes them to ``tests/integration`` the way the old
declaration was meant to.
"""

import pytest
from tests.support.fresh_schema_template import install_fresh_schema_template
from tests.support.navigation_historical_source import historical_sources as historical_sources
from tests.support.server_fixtures import (  # noqa: F401  imported to register them as fixtures here
    _reusable_server,
    api,
    cli,
    restart_server,
    server,
    server_factory,
    stop_server,
)


def pytest_configure(config: pytest.Config) -> None:
    install_fresh_schema_template()
