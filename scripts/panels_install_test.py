"""Installer interruption must wait until the sealed installer completes rollback."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


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


if __name__ == "__main__":
    unittest.main()
