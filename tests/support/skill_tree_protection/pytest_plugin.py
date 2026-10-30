"""Early audit protection and immutable before/after evidence for pytest."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path

import pytest
from _pytest.terminal import TerminalReporter
from tests.support.skill_tree_protection.audit import AuditGuard
from tests.support.skill_tree_protection.contracts import (
    Manifest,
    ManifestDifference,
    ProtectionInventory,
)
from tests.support.skill_tree_protection.inventory import (
    capture_launch_inventory,
    compare_manifests,
    snapshot,
)
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment


class SkillTreeProtectionPlugin:
    def __init__(
        self, inventory: ProtectionInventory, baseline: Manifest, export_root: Path | None = None
    ) -> None:
        self.inventory = inventory
        self.baseline = baseline
        self.guard = AuditGuard(inventory)
        self.guard.install()
        self._homes_storage = tempfile.TemporaryDirectory(prefix="panels-synthetic-homes-")
        self.final: Manifest | None = None
        self.differences: tuple[ManifestDifference, ...] = ()
        self.snapshot_error: str | None = None
        self.export_error: str | None = None
        self._export_root = export_root

    def pytest_configure(self, config: pytest.Config) -> None:
        config.addinivalue_line(
            "markers",
            "real_provider_homes: retain native homes only; audit and manifests remain active",
        )

    def pytest_collection(self) -> None:
        self.guard.set_context("collection")

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item: pytest.Item) -> Iterator[None]:
        self.guard.set_context(item.nodeid)
        homes = (
            {}
            if item.get_closest_marker("real_provider_homes") is not None
            else create_synthetic_homes(Path(self._homes_storage.name))
        )
        try:
            # Fixture scope does not determine when native profiles first resolve.
            # Route before pytest sets up even session- and module-scoped fixtures.
            with synthetic_home_environment(homes):
                yield
        finally:
            self.guard.set_context("session after test teardown")

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        self.guard.set_context("session final manifest")
        try:
            # High-scope fixture teardown can still use an earlier case's homes.
            self._homes_storage.cleanup()
            self.final = snapshot(self.inventory)
            self.differences = compare_manifests(self.baseline, self.final)
        except Exception as error:
            self.snapshot_error = f"{type(error).__name__}: {error}"
        if self.guard.receipts or self.differences or self.snapshot_error:
            if exitstatus in (
                pytest.ExitCode.OK,
                pytest.ExitCode.TESTS_FAILED,
                pytest.ExitCode.NO_TESTS_COLLECTED,
            ):
                session.exitstatus = pytest.ExitCode.TESTS_FAILED

        # The export root comes from initial kernel admission. Reusable detector
        # probes explicitly omit it. Test-created sockets cannot disable export.
        if self._export_root is not None:
            try:
                destination = self._export_root / f"skill-tree-{os.getpid()}.json"
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    json.dumps(
                        {
                            "inventory": asdict(self.inventory),
                            "baseline": asdict(self.baseline),
                            "final": asdict(self.final) if self.final is not None else None,
                            "differences": [asdict(item) for item in self.differences],
                            "receipts": [asdict(item) for item in self.guard.receipts],
                            "snapshot_error": self.snapshot_error,
                        },
                        indent=2,
                    ) + "\n"
                )
            except (OSError, ValueError, TypeError) as error:
                self.export_error = f"{type(error).__name__}: {error}"
                session.exitstatus = pytest.ExitCode.TESTS_FAILED

        # Emit the diagnosis before TerminalReporter's sessionfinish wrapper prints
        # detailed failure tracebacks. terminal_summary itself runs after them.
        terminal = session.config.pluginmanager.get_plugin("terminalreporter")
        if isinstance(terminal, TerminalReporter):
            for receipt in self.guard.receipts:
                terminal.write_line(
                    f"path={receipt.path} operation={receipt.event} test={receipt.node}\n"
                    f"Skill-tree mutation refused: {receipt.reason}",
                    red=True,
                )

    def pytest_terminal_summary(self, terminalreporter: TerminalReporter) -> None:
        receipts = self.guard.receipts
        terminalreporter.write_line(
            f"Skill-tree guard receipts ({len(receipts)}): {list(receipts)}"
        )
        for label, manifest in (("baseline", self.baseline), ("final", self.final)):
            if manifest is not None:
                metrics = manifest.metrics
                terminalreporter.write_line(
                    f"Skill-tree {label}: seconds={metrics.seconds:.6f} "
                    f"entries={metrics.entries} regular_files={metrics.regular_files} "
                    f"hashed_bytes={metrics.hashed_bytes}"
                )
        if self.differences:
            terminalreporter.write_line("Skill-tree manifest differences:", red=True)
            for difference in self.differences:
                terminalreporter.write_line(
                    f"path={difference.path} change={difference.change}",
                    red=True,
                )
            if not receipts:
                terminalreporter.write_line(
                    "No refusal receipt identifies a writer. Source unknown: an unaudited "
                    "child or a legitimate owner change can cause this difference. "
                    "The suite reports the difference without accusation or automatic repair.",
                    red=True,
                )
        if self.snapshot_error:
            terminalreporter.write_line(
                f"Skill-tree manifest failed: {self.snapshot_error}",
                red=True,
            )

        if self.export_error:
            terminalreporter.write_line(
                f"Skill-tree evidence export failed: {self.export_error}", red=True
            )


def bootstrap(*, export_root: Path | None = None) -> SkillTreeProtectionPlugin:
    """Freeze launch roots before collection or any synthetic home replacement."""
    inventory, baseline = capture_launch_inventory(dict(os.environ), Path.cwd())
    return SkillTreeProtectionPlugin(inventory, baseline, export_root)


def pytest_configure(config: pytest.Config) -> None:
    """Allow isolated lifecycle proofs to load the same guard with ``-p``."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    from test_containment.admission import require_containment
    from test_containment.contracts import ContainmentRefusal

    try:
        boundary = require_containment()
    except ContainmentRefusal as error:
        raise pytest.UsageError(f"Test containment refused: {error}") from error
    if not config.pluginmanager.hasplugin("skill-tree-protection"):
        config.pluginmanager.register(
            bootstrap(export_root=boundary.scratch_root / "reports"), "skill-tree-protection"
        )
