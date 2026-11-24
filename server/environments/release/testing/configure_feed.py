"""Configure the disposable home's release-source environment; other jobs keep their own network."""
import os
import plistlib
import subprocess
import sys
from pathlib import Path


def linux_start(root, service, values, restore):
    unit = service + ".service"
    directory = Path.home() / ".config/systemd/user" / (unit + ".d")
    directory.mkdir(exist_ok=True)
    override = directory / "qualification.conf"
    original = root / "installed/app/bin/panels-launcher"
    public_cli = original.with_name("panels")

    def verify(program):
        arguments = subprocess.check_output(["systemctl", "--user", "show", unit, "-p", "ExecStart", "--value"], text=True)
        if f"path={program} ;" not in arguments or f"argv[]={program} serve ;" not in arguments:
            raise RuntimeError("Unexpected disposable home service command: " + str(program))

    verify(public_cli if restore else original)
    if restore:
        # End the catalog experiment. The original unit starts the candidate with no test CA
        # and no update repository, so this last ordinary-use check cannot contact GitHub.
        override.write_text("[Service]\nEnvironment=PLAN_UPDATE_REPOSITORY=\n")
    else:
        # The shipped Linux launcher drops NODE_EXTRA_CA_CERTS before exec. The other
        # shipped serve entry uses the same foreground supervisor and preserves this private CA.
        with override.open("x") as stream:
            stream.write("[Service]\nExecStart=\n" + f'ExecStart="{public_cli}" serve\n')
            stream.writelines(f'Environment="{key}={value}"\n' for key, value in values.items())
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    verify(original if restore else public_cli)
    subprocess.run(["systemctl", "--user", "restart", unit], check=True)


def configure(root, operation, service):
    if os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise RuntimeError("Hosted qualification account required")
    root = root.resolve()
    root.relative_to(Path(os.environ["RUNNER_TEMP"]).resolve())
    if operation == "--restore":
        if sys.platform != "linux":
            raise RuntimeError("Only the Linux proof temporarily selects the other shipped entry")
        return linux_start(root, service, {}, True)
    token = root / "feed/token"
    token.write_text("local-qualification\n")
    token.chmod(0o600)
    values = {
        "PLAN_UPDATE_REPOSITORY": "qualification/panels",
        "PLAN_UPDATE_TOKEN_FILE": str(token),
        "HTTPS_PROXY": "http://127.0.0.1:" + str(int(operation)),
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
        linux_start(root, service, values, False)


if __name__ == "__main__":
    configure(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
