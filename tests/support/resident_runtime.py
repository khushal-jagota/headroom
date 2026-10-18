"""Artifact identity stub for service transaction tests, not wheel qualification."""

import hashlib
import json
from pathlib import Path


def write_runtime_identity(runtime: Path) -> None:
    script = runtime / "bin/panels"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    wheels = runtime / "wheels"
    wheels.mkdir()
    wheel = wheels / "planner-2.0.0-py3-none-any.whl"
    wheel.write_bytes(b"transaction-fixture-only")
    receipt = {
        "distribution": "planner",
        "version": "2.0.0",
        "entrypoint_module": "planner.executor.entrypoint",
        "wheel_filename": wheel.name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "files": {"bin/panels": hashlib.sha256(script.read_bytes()).hexdigest()},
    }
    (runtime / "resident-installed.json").write_text(json.dumps(receipt))
