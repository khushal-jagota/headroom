"""Keep test imports on this source tree before test collection."""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

TREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TREE / "scripts"))

import tree_environment  # noqa: E402  # reached via sys.path.insert above

diagnosis = tree_environment.preflight(TREE)
if diagnosis:
    raise pytest.UsageError(diagnosis)

from test_containment.admission import requested_containment  # noqa: E402
from test_containment.contracts import ContainmentRefusal  # noqa: E402
from test_runtime import (  # noqa: E402
    RuntimeIsolation,
    RuntimeRefusal,
    validate_environment,
)

try:
    _BOUNDARY = requested_containment()
except ContainmentRefusal as error:
    raise pytest.UsageError(
        f"Test containment refused: {error}. Use scripts/run_contained_pytest.py."
    ) from error

# Default discovery omits the full installed build. Explicit paths opt in.
# Containment probes also stay out of ordinary discovery; their conftest refuses
# explicit execution outside a contained process.
collect_ignore = ["opt_in"] + (["containment"] if _BOUNDARY is None else [])

from tests.support.skill_tree_protection.pytest_plugin import bootstrap  # noqa: E402

try:
    # Preserve launch aliases before runtime defaults replace inherited paths.
    _SKILL_TREE_PROTECTION = bootstrap(
        export_root=_BOUNDARY.scratch_root / "reports" if _BOUNDARY is not None else None,
        launch_environment=os.environ,
    )
    _RUNTIME = RuntimeIsolation(
        os.environ, parent=_BOUNDARY.scratch_root / "tmp" if _BOUNDARY is not None else None
    )
    os.environ.clear()
    os.environ.update(_RUNTIME.environment)
    validate_environment(os.environ)
except RuntimeRefusal as error:
    raise pytest.UsageError(f"Test runtime refused: {error}") from error


@pytest.fixture
def executor_transport_home(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Retained fixture for synthetic executor test storage."""
    from tests.support.executor_https import short_transport_root

    from planner.executor.configuration import ExecutorPaths

    with short_transport_root() as root:
        monkeypatch.setattr(ExecutorPaths, "ipc_socket", property(lambda self: root / "ipc.sock"))
        yield root


def pytest_configure(config: pytest.Config) -> None:
    config.add_cleanup(_RUNTIME.close)
    # File transport rejects symlink ancestors, including macOS's /var and /tmp.
    os.environ["TMPDIR"] = str(Path(os.environ.get("TMPDIR", "/tmp")).resolve())
    tempfile.tempdir = None

    config.pluginmanager.register(_SKILL_TREE_PROTECTION, "skill-tree-protection")


@pytest.fixture(autouse=True)
def worker_process_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Worker launches never write custody into an inherited deployment directory."""
    monkeypatch.setenv("PLAN_WORKER_PROCESS_ROOT", str(tmp_path / "worker-processes"))


def _act_as_the_owner_by_default() -> None:
    """Every in-process client of a Panels app carries the owner credential cookie.

    A request with no cookie and no X-Plan identity is unidentified and writes nothing.
    Most tests act as Khushal, who holds the cookie the web app page sets, so a client
    built on an app that has an owner credential starts with it: a ``TestClient`` or an
    ``httpx.AsyncClient`` over ``ASGITransport`` that test code builds. An agent never
    holds Khushal's cookie, so a request that claims an agent identity with
    ``X-Plan-Actor`` is sent without it.
    A test about an unidentified caller clears ``client.cookies``.
    """
    import httpx
    from starlette.testclient import TestClient

    from planner.core.authctx import X_PLAN_ACTOR
    from planner.core.owner_credential import OWNER_CREDENTIAL_COOKIE

    def drop_owner_cookie_from_agent_request(request: httpx.Request) -> None:
        if X_PLAN_ACTOR not in request.headers or "cookie" not in request.headers:
            return
        kept = [
            pair
            for pair in request.headers["cookie"].split("; ")
            if not pair.startswith(f"{OWNER_CREDENTIAL_COOKIE}=")
        ]
        if kept:
            request.headers["cookie"] = "; ".join(kept)
        else:
            del request.headers["cookie"]

    async def drop_owner_cookie_from_async_agent_request(request: httpx.Request) -> None:
        drop_owner_cookie_from_agent_request(request)

    def carry_owner_cookie(client: httpx.Client | httpx.AsyncClient, app: Any, hook: Any) -> None:
        credential = getattr(getattr(app, "state", None), "owner_credential", None)
        if not isinstance(credential, str):
            return
        client.cookies.set(OWNER_CREDENTIAL_COOKIE, credential)
        hooks = client.event_hooks
        hooks["request"] = [*hooks.get("request", []), hook]
        client.event_hooks = hooks

    built_test_client = TestClient.__init__
    built_async_client = httpx.AsyncClient.__init__

    def test_client_with_owner_cookie(
        self: TestClient, app: Any, *args: Any, **kwargs: Any
    ) -> None:
        built_test_client(self, app, *args, **kwargs)
        carry_owner_cookie(self, app, drop_owner_cookie_from_agent_request)

    def async_client_with_owner_cookie(
        self: httpx.AsyncClient, *args: Any, **kwargs: Any
    ) -> None:
        built_async_client(self, *args, **kwargs)
        transport = kwargs.get("transport")
        # Only a client a test builds. Panels' own in-process clients stay as they are.
        built_by_a_test = Path(sys._getframe(1).f_code.co_filename).is_relative_to(TREE / "tests")
        if built_by_a_test and isinstance(transport, httpx.ASGITransport):
            carry_owner_cookie(self, transport.app, drop_owner_cookie_from_async_agent_request)

    TestClient.__init__ = test_client_with_owner_cookie  # type: ignore[method-assign]
    httpx.AsyncClient.__init__ = async_client_with_owner_cookie  # type: ignore[method-assign]


_act_as_the_owner_by_default()
