"""The pin, and the parts of codex's protocol the adapter has hard-coded words for.

Codex's app-server negotiates nothing: no version handshake, no capability probe. A pinned
schema taken from the binary Panels spawns is the whole of the defence, so what is asserted
here is that the pin is intact and that the few strings this adapter writes by hand — the
answers to an approval, the sandbox it asks for — are still strings the pinned protocol
knows. When codex moves, these fail, which is the point.
"""

from __future__ import annotations

import shutil

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
