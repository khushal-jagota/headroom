from __future__ import annotations

import importlib

migration = importlib.import_module(
    "planner.core.migrations.versions.initial_orchestrator_working_set"
)
