"""Resolve resident paths without touching account state."""

from pathlib import Path

from planner.environments.resident.contracts import ResidentPaths
from planner.executor.configuration import EXECUTOR_CODE_RELATIVE_PATH, ExecutorPaths


def resident_paths(home: Path, executor_root: Path | None = None) -> ResidentPaths:
    home = home.absolute()
    return ResidentPaths(
        home=home,
        deployment_root=(home / EXECUTOR_CODE_RELATIVE_PATH).parent.parent,
        executor_root=executor_root or home / "Library/Application Support/Panels/executor",
    )


def data_paths(paths: ResidentPaths) -> ExecutorPaths:
    return ExecutorPaths(paths.executor_root)
