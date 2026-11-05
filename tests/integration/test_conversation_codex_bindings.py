"""The pin, and the parts of codex's protocol the adapter has hard-coded words for.

Codex's app-server negotiates nothing: no version handshake, no capability probe. A pinned
schema taken from the binary Panels spawns is the whole of the defence, so what is asserted
here is that the pin is intact and that the few strings this adapter writes by hand — the
answers to an approval, the sandbox it asks for — are still strings the pinned protocol
knows. When codex moves, these fail, which is the point.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from planner.conversation.backends.codex_app_server import generate_bindings as generator
from planner.conversation.backends.codex_app_server.generate_bindings import (
    PINNED_CODEX_CLI_VERSION,
)

CODEX_EXECUTABLE = shutil.which("codex")
PINNED_CODEX_AVAILABLE = (
    CODEX_EXECUTABLE is not None
    and generator._installed_codex_version(CODEX_EXECUTABLE) == PINNED_CODEX_CLI_VERSION
)
pinned_codex_only = pytest.mark.skipif(
    not PINNED_CODEX_AVAILABLE,
    reason="the pinned Codex binary is not installed",
)


@pinned_codex_only
def test_turn_bindings_regenerate_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Targeted app/read models must match the pinned CLI, not handwritten wire shapes."""
    original = generator.TURN_BUNDLE
    # Ruff discovers configuration from the generated file location. Give the
    # scratch output the same formatter settings as the checked-in artifact.
    project_root = Path(__file__).resolve().parents[2]
    (tmp_path / "pyproject.toml").write_bytes((project_root / "pyproject.toml").read_bytes())
    target = replace(
        original,
        vendored_subset_schema_path=tmp_path / original.vendored_subset_schema_path.name,
        generated_bindings_path=tmp_path / original.generated_bindings_path.name,
    )
    monkeypatch.setattr(generator, "BUNDLES", (target,))
    assert CODEX_EXECUTABLE is not None
    generator.generate_bindings(codex_executable=CODEX_EXECUTABLE, bundle_name="turn")
    assert (
        target.vendored_subset_schema_path.read_bytes()
        == original.vendored_subset_schema_path.read_bytes()
    )
    assert (
        target.generated_bindings_path.read_bytes() == original.generated_bindings_path.read_bytes()
    )
