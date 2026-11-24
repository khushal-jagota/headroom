"""Configure the disposable home's release-source environment; other jobs keep their own network."""
import os
import plistlib
import subprocess
import sys
from pathlib import Path


def configure(root, port, service):
    if os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise RuntimeError("Hosted qualification account required")
    root = root.resolve()
    root.relative_to(Path(os.environ["RUNNER_TEMP"]).resolve())
    token = root / "feed/token"
    token.write_text("local-qualification\n")
    token.chmod(0o600)
    values = {
        "PLAN_UPDATE_REPOSITORY": "qualification/panels",
        "PLAN_UPDATE_TOKEN_FILE": str(token),
        "HTTPS_PROXY": "http://127.0.0.1:" + str(int(port)),
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "NODE_USE_ENV_PROXY": "1",
        "NODE_EXTRA_CA_CERTS": str(root / "feed/cert.pem"),
    }
    if sys.platform == "darwin":
        plist = root / "installed/agents" / (service + ".plist")
        data = plistlib.loads(plist.read_bytes())
        assert data["Label"] == service and data["WorkingDirectory"] == str(root / "installed")
        data["EnvironmentVariables"].update(values)
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain + "/" + service], check=True)
        plist.write_bytes(plistlib.dumps(data))
        subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)
    else:
        unit = service + ".service"
        directory = Path.home() / ".config/systemd/user" / (unit + ".d")
        directory.mkdir()
        (directory / "qualification.conf").write_text("[Service]\n" + "".join(
            f'Environment="{key}={value}"\n' for key, value in values.items()))
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "restart", unit], check=True)


if __name__ == "__main__":
    configure(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
