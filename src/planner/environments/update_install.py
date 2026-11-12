"""Server-side steps around the installer: schema check, backup decision, launch command.

The publication rehearsal uses the same helpers, so it installs exactly as the server does.
"""

from __future__ import annotations

import ast
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from planner.environments.update_configuration import INSTALLER_FILE_NAME

_INSTALLER_SOURCE = Path(__file__).with_name("update_installer.py")
_HOST_PYTHON_PATH = "/usr/local/bin:/usr/bin:/bin"


def migration_graph(app_root: Path) -> dict[str, tuple[str, ...]]:
    """Read revision -> parents from the staged app's migration files without importing them.

    Importing a candidate's migrations would load its modules into this process.
    """
    graph: dict[str, tuple[str, ...]] = {}
    for path in sorted((app_root / "src/planner/core/migrations/versions").glob("*.py")):
        values: dict[str, object] = {}
        for node in ast.parse(path.read_text()).body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in {"revision", "down_revision"}
            ):
                values[node.targets[0].id] = ast.literal_eval(node.value)
        revision, parents = values.get("revision"), values.get("down_revision")
        if not isinstance(revision, str):
            continue
        if parents is None:
            graph[revision] = ()
        elif isinstance(parents, str):
            graph[revision] = (parents,)
        elif isinstance(parents, tuple) and all(isinstance(item, str) for item in parents):
            graph[revision] = tuple(str(item) for item in parents)
        else:
            raise ValueError(f"unreadable migration parents in {path.name}")
    return graph


def schema_change_pending(database: Path, staged_app: Path) -> bool:
    """True when the candidate migrates this database; refuse a revision it does not know."""
    graph = migration_graph(staged_app)
    heads = set(graph) - {parent for parents in graph.values() for parent in parents}
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    revisions = {str(row[0]) for row in rows}
    if not revisions or not revisions <= set(graph):
        raise ValueError("This release does not know the database's schema revision.")
    return revisions != heads


def own_service_unit(cgroup: str | None = None) -> str | None:
    """Name the systemd service this process runs in, or None outside one.

    The installer stops and starts exactly this unit, so a server can only ever restart
    itself. A copy of Panels running anywhere else cannot stop the live service.
    """
    if cgroup is None:
        try:
            cgroup = Path("/proc/self/cgroup").read_text()
        except OSError:
            return None
    for line in cgroup.splitlines():
        hierarchy, _, path = line.partition("::")
        if hierarchy != "0":
            continue
        leaf = path.rstrip("/").rsplit("/", 1)[-1]
        if leaf.endswith(".service") and not leaf.startswith("user@"):
            return leaf
    return None


def write_installer(data_directory: Path) -> Path:
    """Copy the installer out of this app, so the app swap cannot change the code it runs."""
    destination = data_directory / INSTALLER_FILE_NAME
    temporary = destination.with_name(f".{destination.name}.tmp")
    shutil.copyfile(_INSTALLER_SOURCE, temporary)
    temporary.replace(destination)
    return destination


def installer_command(
    installer: Path,
    *,
    current_root: Path,
    attempt_id: str,
    service: str,
    health_url: str,
    health_timeout_seconds: float,
) -> list[str]:
    python = shutil.which("python3", path=_HOST_PYTHON_PATH)
    if python is None:
        raise OSError("The host has no python3 for the installer.")
    return [
        python, "-I", "-S", "-B", str(installer),
        "--current-root", str(current_root),
        "--attempt-id", attempt_id,
        "--service", service,
        "--health-url", health_url,
        "--health-timeout-seconds", str(health_timeout_seconds),
    ]
