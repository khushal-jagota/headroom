"""Private runtime defaults for repository tests, independent of a worker's shell.

This is startup isolation, not a filesystem or network sandbox. Tests can supply
explicit fixture configuration after startup. No environment marker skips it.
"""

from __future__ import annotations

import ipaddress
import json
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType
from urllib.parse import urlsplit

DEAD_SERVER_URL = "http://127.0.0.1:0"
_EVIDENCE = "PANELS_TEST_ORIGINAL_PATHS"
_HOME_KEYS = {"HOME", "CODEX_HOME", "HERMES_HOME", "CLAUDE_CONFIG_DIR"}
_PATH_SUFFIXES = ("_PATH", "_DIR", "_ROOT", "_HOME", "_DATABASE", "_SOCKET")


class RuntimeRefusal(ValueError):
    """A test runtime still names deployment storage or the live server."""


def _is_path(name: str, value: str) -> bool:
    return name.endswith(_PATH_SUFFIXES) or value.startswith(("/", "./", "../", "~/"))


def evidence_environments(environment: Mapping[str, str]) -> tuple[dict[str, str], ...]:
    """Retain path roots and their original home context for read-only inventory."""
    snapshots: list[dict[str, str]] = []
    if raw := environment.get(_EVIDENCE):
        try:
            original = json.loads(raw)
        except (ValueError, TypeError) as error:
            raise RuntimeRefusal("Invalid original test path evidence") from error
        if not isinstance(original, list):
            raise RuntimeRefusal("Invalid original test path evidence")
        for snapshot in original:
            if not isinstance(snapshot, dict) or any(
                not isinstance(key, str) or not isinstance(value, str)
                or not (key in _HOME_KEYS or key.startswith("PLAN_") and _is_path(key, value))
                for key, value in snapshot.items()
            ):
                raise RuntimeRefusal("Invalid original test path evidence")
            snapshots.append({
                **{name: value for name, value in environment.items()
                   if not name.startswith("PLAN_")},
                **snapshot,
            })
    snapshots.append(dict(environment))
    return tuple(snapshots)


def evidence_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Recover original provider selections, never an isolation exemption."""
    return evidence_environments(environment)[0]


def validate_environment(
    environment: Mapping[str, str], protected_paths: Sequence[Path] = (),
) -> None:
    """Refuse residual deployment paths, resolved aliases, and the live URL."""
    for name, value in environment.items():
        if not name.startswith("PLAN_"):
            continue
        if name == "PLAN_SERVER_URL":
            if not value.strip():
                raise RuntimeRefusal("Empty PLAN_SERVER_URL restores the live CLI default")
            try:
                url = urlsplit(value)
                host = (url.hostname or "").lower().rstrip(".")
                port = url.port
                loopback = host == "localhost"
                try:
                    loopback = loopback or ipaddress.ip_address(host).is_loopback
                except ValueError:
                    pass
            except ValueError as error:
                raise RuntimeRefusal("Invalid PLAN_SERVER_URL in test runtime") from error
            if loopback and port == 8767:
                raise RuntimeRefusal(f"Test runtime refused live server: {name}={value}")
        elif value and _is_path(name, value):
            path = Path(value).expanduser().absolute()
            try:
                resolved = path.resolve()
                forbidden = "Deployments" in path.parts or "Deployments" in resolved.parts
                for protected in protected_paths:
                    target = protected.resolve()
                    forbidden = forbidden or resolved == target or resolved.is_relative_to(target)
                    if path.exists() and protected.exists():
                        forbidden = forbidden or path.samefile(protected)
            except (OSError, RuntimeError) as error:
                raise RuntimeRefusal(f"Cannot resolve test runtime path: {name}={value}") from error
            if forbidden:
                raise RuntimeRefusal(f"Test runtime refused deployment path: {name}={value}")


def environment_for(root: Path, original: Mapping[str, str]) -> dict[str, str]:
    """Prepare defaults in caller-owned storage before fixture-specific overrides."""
    root = root.resolve()
    validate_environment({"PLAN_TEST_RUNTIME_ROOT": str(root)})
    root.mkdir(parents=True, exist_ok=True)
    directories = {
        "PLAN_LOGS_DIR": root / "logs",
        "PLAN_BACKUP_DIR": root / "backups",
        "PLAN_HERMES_HOME": root / "hermes",
        "PLAN_WORKSPACE_ROOT": root / "workspace",
        "PLAN_TICKET_SCRATCH_ROOT": root / "tickets",
        "PLAN_TICKET_FILES_DIR": root / "files",
    }
    paths = {
        **directories,
        "PLAN_CONFIG_PATH": root / "test-config.yaml",
        "PLAN_DB_PATH": root / "planning.db",
        "PLAN_CREDENTIAL_KEY_PATH": root / "credential-key",
        "PLAN_DISPATCHER_LOCK_PATH": root / "dispatcher.lock",
        "PLAN_SERVER_CONTROL_SOCKET": root / "control.sock",
        "PLAN_SERVER_LIFECYCLE_LEASE_PATH": root / "lifecycle-lease.json",
        "PLAN_TICKET_SCRATCH_DATABASE": root / "planning.db",
    }
    # Resolve every child before the first mkdir or write. Existing aliases in
    # caller-owned storage must not redirect even an empty config into deployment.
    validate_environment({name: str(path) for name, path in paths.items()})
    for path in directories.values():
        path.mkdir(parents=True, exist_ok=True)
    (root / "test-config.yaml").write_text("{}\n")
    evidence = evidence_environments(original)
    result = {name: value for name, value in original.items() if not name.startswith("PLAN_")}
    result[_EVIDENCE] = json.dumps([
        {name: value for name, value in snapshot.items()
         if name in _HOME_KEYS or name.startswith("PLAN_") and _is_path(name, value)}
        for snapshot in evidence
    ])
    result.update({name: str(path) for name, path in paths.items()})
    result.update(
        PLAN_SERVER_URL=DEAD_SERVER_URL,
        PLAN_TEST_MODE="1",
        PLAN_DISPATCH_ENABLED="false",
        PLAN_PORT="0",
    )
    validate_environment(result)
    return result


class RuntimeIsolation:
    """Keep private storage alive for one test process or child command."""

    def __init__(self, original: Mapping[str, str], *, parent: Path | None = None) -> None:
        # Only a caller with kernel admission supplies a parent. An inherited
        # TMPDIR or serialized marker cannot choose or bypass the private root.
        parent = Path("/tmp").resolve() if parent is None else parent.resolve()
        validate_environment({"PLAN_TEST_RUNTIME_ROOT": str(parent)})
        self._storage = tempfile.TemporaryDirectory(prefix="panels-test-", dir=parent)
        self.root = Path(self._storage.name)
        try:
            self.environment = environment_for(self.root, original)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self._storage.cleanup()

    def __enter__(self) -> RuntimeIsolation:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
