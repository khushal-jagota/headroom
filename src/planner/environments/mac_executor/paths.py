"""Resolve the fixed installation paths without touching account state."""

from pathlib import Path

from planner.environments.mac_executor.contracts import MacExecutorPaths


def mac_executor_paths(home: Path, executor_root: Path | None = None) -> MacExecutorPaths:
    home = home.absolute()
    deployment = home / "Deployments/PanelsExecutor"
    return MacExecutorPaths(
        home=home,
        source=deployment / "source",
        venv=deployment / "venv",
        executor_root=executor_root or home / "Library/Application Support/Panels/executor",
    )
