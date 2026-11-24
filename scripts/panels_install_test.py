"""Installer interruption must wait until the sealed installer completes rollback."""
from __future__ import annotations

import os
import copy
import zipfile
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import panels_install


class InstallerInterruption(unittest.TestCase):
    def test_term_reaches_installer_and_waits_for_rollback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="panels-bootstrap-test-") as temporary:
            ready = Path(temporary) / "ready"
            cleaned = Path(temporary) / "cleaned"
            # A regressed bootstrap can die before it forwards TERM. The child remains
            # bounded even in that failure, and the outer test retains its own process.
            child = """
import signal, sys, time
from pathlib import Path
ready, cleaned = map(Path, sys.argv[1:])
def stop(signum, frame):
    time.sleep(0.1)
    cleaned.write_text('rollback complete')
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
ready.write_text('ready')
sys.stdin.read()
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    time.sleep(0.01)
"""
            wrapper = """
import os, sys
sys.path.insert(0, sys.argv[1])
from panels_install import run_installer
run_installer([sys.executable, '-c', sys.argv[2], *sys.argv[3:]], '{}', dict(os.environ))
"""
            process = subprocess.Popen(
                [sys.executable, "-c", wrapper, str(Path(__file__).parent.resolve()), child,
                 str(ready), str(cleaned)],
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists(), "The installer child did not start")
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 128 + signal.SIGTERM, (stdout, stderr))
                self.assertEqual(cleaned.read_text(), "rollback complete")
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)


class ServerWithoutHostNode(unittest.TestCase):
    # Existing release-source and interruption tests do not exercise prerequisites.
    # Prove both platform preflights never select or execute the account's Node.
    def test_home_preflight_needs_no_node(self) -> None:
        import argparse
        with tempfile.TemporaryDirectory(prefix="panels-no-host-node-") as temporary:
            home = Path(temporary)
            args = argparse.Namespace(command="home", root=home / "new", service="proof", port=18767)
            def run(*arguments, **kwargs):
                self.assertNotIn("node", " ".join(arguments))
                output = "yes" if arguments[0] == "loginctl" else "not-found"
                return subprocess.CompletedProcess(arguments, 0 if arguments[0] == "loginctl" else 1, output, "Could not find service")
            def which(name, **kwargs):
                self.assertNotEqual(name, "node")
                return "/usr/bin/" + name
            with patch.object(Path, "home", return_value=home), \
                 patch.object(panels_install, "_run", run), \
                 patch.object(panels_install.shutil, "which", which), \
                 patch.object(panels_install.platform, "mac_ver", return_value=("15.0", (), "")), \
                 patch.object(panels_install.socket, "socket"):
                for target in ("linux", "darwin"):
                    with self.subTest(target=target):
                        panels_install.preflight(args, target)


class PermanentReleaseSource(unittest.TestCase):
    # Existing rollback coverage never resolves release metadata or a package source.
    # This adapter boundary proves each role survives removal of Actions artifacts.
    def setUp(self) -> None:
        self.sha = "a" * 40
        self.receipt = {
            "artifact_source": "github-release",
            "artifact_id": 11,
            "publication_run_id": 123,
            "expires_at": None,
            "archive_sha256": "b" * 64,
            "manifest": {
                "app_sha": self.sha,
                "runtime_contract": {"version": 2, "system": "linux", "architecture": "x86_64"},
            },
            "source_run": {
                "repository": panels_install.REPOSITORY,
                "source_sha": self.sha,
                "run_id": "123",
                "run_attempt": "2",
                "event": "push",
                "ref": "refs/heads/main",
                "workflow_path": ".github/workflows/publish-typescript-release.yml",
                "workflow_ref": f"{panels_install.REPOSITORY}/.github/workflows/publish-typescript-release.yml@refs/heads/main",
            },
        }
        self.receipt["mac_home"] = {
            **self.receipt,
            "artifact_id": 12,
            "manifest": {
                "app_sha": self.sha,
                "runtime_contract": {
                    "version": 2, "system": "darwin", "architecture": "arm64", "role": "home",
                },
            },
        }
        for name, identifier in [("mac", 13), ("linux_executor", 14)]:
            self.receipt[name] = {
                "artifact_source": "github-release", "artifact_id": identifier,
                "archive_sha256": "c" * 64, "archive_size": 7,
            }
        self.release = {
            "tag_name": f"server-{self.sha}-123-2",
            "target_commitish": self.sha,
            "draft": False,
            "prerelease": False,
            "assets": [
                {"name": name, "id": identifier}
                for name, identifier in [
                    ("publication.json", 10), ("app.zip", 11), ("mac-home.zip", 12),
                    ("mac.zip", 13), ("linux-executor.zip", 14),
                ]
            ],
        }
        self.requests: list[str] = []

    def api(self, path, destination=None, accept="application/vnd.github+json"):
        self.requests.append(path)
        if path == "releases?per_page=100&page=1":
            return [{"tag_name": "desktop-v1"}, {**self.release, "draft": True}, self.release]
        self.assertEqual(accept, "application/octet-stream")
        if path == "releases/assets/10":
            return copy.deepcopy(self.receipt)
        self.assertIn(path, [f"releases/assets/{identifier}" for identifier in (11, 12, 13, 14)])
        self.assertIsNotNone(destination)
        with zipfile.ZipFile(destination, "w") as zipped:
            zipped.writestr("panels-app.tar.gz", b"archive")

    def test_downloads_all_roles_without_actions_metadata(self) -> None:
        with patch.object(panels_install, "_api", self.api):
            for target, command, identifier in [
                ("linux", "install", 11), ("darwin", "install", 12),
                ("darwin", "join", 13), ("linux", "join", 14),
            ]:
                with self.subTest(target=target, command=command):
                    with tempfile.TemporaryDirectory(prefix="panels-release-source-") as temporary:
                        work = Path(temporary)
                        receipt = panels_install.find_release(target, command, None, work)
                        self.assertEqual(receipt["artifact_id"], identifier)
                        archive = panels_install.download_release(receipt, work)
                        self.assertEqual(archive.read_bytes(), b"archive")
        self.assertFalse(any(path.startswith("actions/") for path in self.requests))

    def test_rejects_provenance_and_asset_substitution(self) -> None:
        for change in ("workflow", "target", "asset"):
            with self.subTest(change=change):
                original = copy.deepcopy(self.receipt)
                target = self.release["target_commitish"]
                if change == "workflow":
                    self.receipt["source_run"]["workflow_path"] = ".github/workflows/untrusted.yml"
                elif change == "target":
                    self.release["target_commitish"] = "d" * 40
                else:
                    self.receipt["artifact_id"] = 13
                with patch.object(panels_install, "_api", self.api):
                    with self.assertRaisesRegex(ValueError, "identity differs"):
                        panels_install.find_release("linux", "install", None, Path("unused"))
                self.receipt = original
                self.release["target_commitish"] = target

    def test_requested_revision_scans_past_a_full_page(self) -> None:
        # A machine can return after more than one page of later desktop releases.
        def paged(path, destination=None, accept="application/vnd.github+json"):
            if path == "releases?per_page=100&page=1":
                return [{"tag_name": "desktop-v1"}] * 100
            if path == "releases?per_page=100&page=2":
                return [self.release]
            return self.api(path, destination, accept)
        with patch.object(panels_install, "_api", paged):
            selected = panels_install.find_release("linux", "install", self.sha, Path("unused"))
            self.assertEqual(selected["revision"], self.sha)


if __name__ == "__main__":
    unittest.main()
