"""Version-neutral isolation and actual-application fixtures for browser measurement.

This module uses only the standard library until build_application is called in a
fresh sanitized child. It does not supply a navigation authority or replacement UI.
"""

from __future__ import annotations

import importlib
import json
import math
import os
import statistics
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI


EVIDENCE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        "evidence_schema_version",
        "source",
        "mode",
        "revision",
        "isolated_paths",
        "seed",
        "samples",
        "requests",
        "network",
        "writes",
        "limits",
        "final_canonical_dom_equality",
        "built_asset_hashes",
        "paired_cost_window_ns",
    ],
    "properties": {
        "evidence_schema_version": {"const": 1},
        "source": {"type": "string"},
        "revision": {"type": "string"},
        "mode": {"enum": ["baseline", "candidate"]},
        "samples": {"type": "array", "items": {"type": "object", "required": ["scenario"]}},
        "requests": {"type": "array"},
        "network": {"type": "array"},
        "writes": {"type": "array"},
        "limits": {"type": "array"},
        "isolated_paths": {"type": "object"},
        "built_asset_hashes": {"type": "object"},
        "final_canonical_dom_equality": {
            "type": "object",
            "required": ["rows", "equal"],
            "properties": {"equal": {"const": True}, "rows": {"type": "integer", "minimum": 2}},
        },
        "paired_cost_window_ns": {
            "type": "array",
            "minItems": 2,
            "maxItems": 2,
            "items": {"type": "integer"},
        },
    },
}


def distribution(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0}
    return {
        "count": len(values),
        "minimum": ordered[0],
        "median": statistics.median(values),
        "p95_nearest_rank": ordered[math.ceil(len(values) * 0.95) - 1],
        "maximum": ordered[-1],
    }


def isolated_environment(root: Path, source: Path, inherited: Mapping[str, str]) -> dict[str, str]:
    """Create all runtime state paths before any application import in the child."""
    # Inherit only process-loader inputs. A denylist misses unrelated provider
    # credentials and future runtime homes that a fixture must never receive.
    allowed = {"PATH", "LD_LIBRARY_PATH", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE"}
    env = {key: value for key, value in inherited.items() if key in allowed}
    for key, name in {
        "HOME": "home",
        "XDG_RUNTIME_DIR": "xdg-runtime",
        "XDG_CACHE_HOME": "cache",
        "XDG_CONFIG_HOME": "config",
        "XDG_DATA_HOME": "data",
        "XDG_STATE_HOME": "state",
        "CODEX_HOME": "codex",
        "HERMES_HOME": "hermes",
        "CLAUDE_CONFIG_DIR": "claude",
        "PLAN_HERMES_HOME": "hermes",
        "PLAN_LOGS_DIR": "logs",
        "PLAN_BACKUP_DIR": "backups",
    }.items():
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        env[key] = str(directory)
    (root / "workspace").mkdir(exist_ok=True)
    env.update(
        {
            "PLAN_DB_PATH": str(root / "synthetic.db"),
            "PLAN_DISPATCHER_LOCK_PATH": str(root / "dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(root / "control.sock"),
            "PLAN_TEST_MODE": "1",
            "PLAN_DISPATCH_ENABLED": "0",
            "PLAN_APP_ROOT": str(source),
            "PLAN_FAKE_NOW": "2026-07-04T12:00:00+00:00",
            "TZ": "UTC",
            "LANG": "C.UTF-8",
        }
    )
    return env


def assert_isolated(root: Path, source: Path) -> dict[str, str]:
    required = {
        "HOME",
        "XDG_RUNTIME_DIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "CODEX_HOME",
        "HERMES_HOME",
        "CLAUDE_CONFIG_DIR",
        "PLAN_DB_PATH",
        "PLAN_LOGS_DIR",
        "PLAN_DISPATCHER_LOCK_PATH",
        "PLAN_SERVER_CONTROL_SOCKET",
        "PLAN_HERMES_HOME",
    }
    if not required.issubset(os.environ):
        raise RuntimeError("incomplete whole-runtime isolation")
    if Path(os.environ["PLAN_DB_PATH"]).resolve() != root.resolve() / "synthetic.db":
        raise RuntimeError("database is not the dedicated synthetic fixture")
    paths = {
        key: value
        for key, value in os.environ.items()
        if key in {"HOME", "CODEX_HOME", "HERMES_HOME", "CLAUDE_CONFIG_DIR"}
        or key.startswith("XDG_")
        or key
        in {
            "PLAN_DB_PATH",
            "PLAN_LOGS_DIR",
            "PLAN_BACKUP_DIR",
            "PLAN_HERMES_HOME",
            "PLAN_DISPATCHER_LOCK_PATH",
            "PLAN_SERVER_CONTROL_SOCKET",
        }
    }
    for key, value in paths.items():
        if not Path(value).resolve().is_relative_to(root.resolve()):
            raise RuntimeError(f"unsanitized runtime path: {key}")
    planner = importlib.import_module("planner")
    module_file = planner.__file__
    if not isinstance(module_file, str) or not Path(module_file).resolve().is_relative_to(
        source.resolve()
    ):
        raise RuntimeError("interpreter imports a different source tree")
    return paths


def build_application(
    root: Path, source: Path, mode: str, count: int
) -> tuple[FastAPI, dict[str, Any]]:
    """Compose the real application; only backend wire/probe adapters are controlled."""
    assert_isolated(root, source)
    server: Any = importlib.import_module("planner.core.server")
    db = importlib.import_module("planner.core.db")
    config_module = importlib.import_module("planner.core.config")
    clock_module = importlib.import_module("planner.core.clock")
    fixtures = importlib.import_module("tests.e2e.conversation_signals_server")
    contracts = importlib.import_module("planner.conversation.contracts")
    snapshots = importlib.import_module("planner.conversation.snapshot")
    # The builder moved out of the HTTP module on the candidate. The baseline
    # retains its original builder. Neither case constructs a second system.
    builder: Any = importlib.import_module(
        "planner.conversation.runtime_composition"
        if mode == "candidate"
        else "planner.conversation.api"
    )
    children: dict[str, Any] = {}

    def child_factory(*, resolved_start: Any, event_sink: Any, message_files: Any) -> Any:
        child = fixtures.ScriptedChild(event_sink)
        children[resolved_start.conversation_id] = child
        return child

    server.production_backend_child_factories = lambda **_kwargs: {
        key: child_factory for key in contracts.ConversationBackendKey
    }
    builder.BackendSnapshotService = lambda **kwargs: snapshots.BackendSnapshotService(
        fixtures.NoInstalledBackends(), **kwargs
    )
    config = config_module.load_config(None, env=os.environ)
    path = Path(config.db_path)
    if mode == "baseline" and not path.exists():
        with closing(db.connect(str(path))) as connection:
            db.create_schema(connection)
    app: FastAPI = server.create_app(
        config, clock_module.build_clock(config), lambda: db.connect(str(path))
    )
    seed_path = root / "seed.json"
    state: dict[str, Any] = {
        "children": children,
        "path": path,
        "config": config,
        "seed": json.loads(seed_path.read_text()) if seed_path.exists() else None,
    }
    configured = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: Any) -> AsyncIterator[None]:
        async with configured(application):
            if state["seed"] is None:
                state["seed"] = seed_application(application, root, count)
                seed_path.write_text(json.dumps(state["seed"], indent=2) + "\n")
            yield

    app.router.lifespan_context = lifespan
    return app, state


def seed_application(app: Any, root: Path, count: int) -> dict[str, Any]:
    """Use canonical source doors after actual startup and before browser admission."""
    db = importlib.import_module("planner.core.db")
    catalogue = importlib.import_module("tests.support.model_catalogue")
    projects = importlib.import_module("planner.projects.data")
    items = importlib.import_module("planner.sprints.data")
    tickets = importlib.import_module("planner.tickets.data")
    contracts = importlib.import_module("planner.core.contracts")
    result: dict[str, Any] = {"tickets": [], "items": [], "logical_titles": []}
    with closing(db.connect(app.state.config.db_path)) as conn:
        catalogue.seed_test_model_catalogue(conn)
        conn.commit()
        project = projects.create_project(
            conn,
            name="Measured workspace",
            summary="Synthetic",
            folder_path=root / "workspace",
            priority=contracts.Priority.P2,
            now=1,
        )
        for name, priority in (
            ("Measured Alpha", contracts.Priority.P1),
            ("Measured Beta", contracts.Priority.P2),
        ):
            item = items.create_item(
                conn, title=name, project_id=project.id, priority=priority, clock=app.state.clock
            )
            result["items"].append(item.id)
        for index in range(count):
            title = f"Measured ticket {index:04d}"
            ticket = tickets.create_ticket(
                conn,
                title=title,
                principal=contracts.OWNER_PRINCIPAL,
                now=1000 + index,
                title_max_chars=200,
                project_id=project.id,
                sprint_item_id=result["items"][0],
                worker_type="coding",
            )
            result["tickets"].append(ticket.id)
            result["logical_titles"].append(title)
        result["project"] = project.id
    return result


def rss_bytes(pid: int) -> int | None:
    try:
        resident_pages = int(Path(f"/proc/{pid}/statm").read_text().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


def main() -> None:
    # Keep one runner rather than maintain a second stale backend-only assembly.
    from navigation_browser_harness import main as browser_main

    browser_main()


if __name__ == "__main__":
    main()
