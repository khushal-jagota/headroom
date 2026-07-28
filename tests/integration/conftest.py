"""Share the real-server fixture without classifying process tests as browser E2E."""

pytest_plugins = ("tests.e2e.conftest",)
