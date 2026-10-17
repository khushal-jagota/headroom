"""Install dormant hooks through the isolated E2E child PYTHONPATH only."""

import os

if os.environ.get("PLAN_TEST_MODE") == "1" and os.environ.get("PANELS_E2E_OWNER_READ_TRACE"):
    from tests.e2e.approval_message_server_trace import install

    install()
