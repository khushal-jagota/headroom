"""Run the shipped Node client's proxy transport against the isolated catalog."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class LocalFeedTest(unittest.TestCase):
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
