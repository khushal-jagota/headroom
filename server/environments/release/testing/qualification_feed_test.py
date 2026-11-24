"""Run the shipped Node client's proxy transport against the isolated catalog."""
import os
import plistlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from configure_feed import mac_feed


class LocalFeedTest(unittest.TestCase):
    def test_mac_reloads_the_original_agent_only_after_the_job_and_supervisor_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "installed"
            plist = home / "agents/proof.plist"
            program = home / "app/bin/panels"
            plist.parent.mkdir(parents=True)
            original = {"Label": "proof", "WorkingDirectory": str(home),
                        "ProgramArguments": [str(program), "serve"], "EnvironmentVariables": {"KEPT": "yes"}}
            plist.write_bytes(plistlib.dumps(original))
            loaded = f"path = {plist}\nprogram = {program}\npid = 1234\n"
            found = subprocess.CompletedProcess([], 0, loaded, "")
            gone = subprocess.CompletedProcess([], 113, "", "Could not find service")
            ok = subprocess.CompletedProcess([], 0, "", "")
            with (patch("configure_feed.subprocess.run", side_effect=[found, ok, found, gone, gone, ok, found]) as run,
                  patch("configure_feed.os.kill", side_effect=[None, ProcessLookupError]) as observe,
                  patch("configure_feed.time.sleep")):
                mac_feed(root, "proof", {"PLAN_UPDATE_REPOSITORY": "qualification/panels"})
            self.assertEqual([call.args[0][1] for call in run.call_args_list],
                             ["print", "bootout", "print", "print", "print", "bootstrap", "print"])
            self.assertEqual([call.args for call in observe.call_args_list], [(1234, 0)] * 2)
            self.assertEqual(plistlib.loads(plist.read_bytes()), {
                **original, "EnvironmentVariables": {"KEPT": "yes", "PLAN_UPDATE_REPOSITORY": "qualification/panels"}})

            denied = subprocess.CompletedProcess([], 113, "", "Permission denied")
            with patch("configure_feed.subprocess.run", side_effect=[found, ok, denied]) as run:
                with self.assertRaisesRegex(RuntimeError, "Permission denied"):
                    mac_feed(root, "proof", {})
            self.assertNotIn("bootstrap", [call.args[0][1] for call in run.call_args_list])

            with (patch("configure_feed.subprocess.run", side_effect=[found, ok, found]) as run,
                  patch("configure_feed.time.sleep"), patch("configure_feed.time.monotonic", side_effect=[0, 0, 0, 0, 121])):
                with self.assertRaisesRegex(TimeoutError, "Timed out reloading"):
                    mac_feed(root, "proof", {})
            self.assertEqual([call.args[0][1] for call in run.call_args_list], ["print", "bootout", "print"])

    def test_serves_only_the_qualification_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "releases.json").write_text("[]")
            proc = subprocess.Popen(["python3", Path(__file__).with_name("qualification_feed.py"), root],
                                    stdout=subprocess.PIPE, text=True)
            try:
                port = int(proc.stdout.readline())
                env = {"PATH": os.environ["PATH"], "NODE_USE_ENV_PROXY": "1",
                       "HTTPS_PROXY": f"http://127.0.0.1:{port}", "NODE_EXTRA_CA_CERTS": str(root / "cert.pem")}
                script = """
                  import assert from 'node:assert/strict';
                  for (const [path, token, status] of [
                    ['qualification/panels/releases','local-qualification',200],
                    ['qualification/panels/releases','another-token',403],
                    ['another/repo/releases','local-qualification',403],
                    ['qualification/panels/releases/assets/99','local-qualification',404],
                  ]) {
                    const response = await fetch('https://api.github.com/repos/' + path, {
                      headers: {Authorization: 'Bearer ' + token}, signal: AbortSignal.timeout(3000)
                    });
                    assert.equal(response.status,status);
                    if (response.ok) assert.deepEqual(await response.json(),[]);
                  }"""
                subprocess.run(["node", "--input-type=module", "-e", script], env=env, check=True)
            finally:
                proc.terminate()
                proc.wait()
                proc.stdout.close()


if __name__ == "__main__":
    unittest.main()
