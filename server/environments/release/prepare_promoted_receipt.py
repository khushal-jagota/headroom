"""Download immutable Actions ZIPs without repacking them for permanent publication."""
import json
import os
from pathlib import Path
import subprocess

qualified = json.loads(os.environ["QUALIFIED"])
ids = {**qualified["artifacts"], "qualified-mac": qualified["artifacts"]["mac"],
       "mac": int(os.environ["SIGNED_MAC_ID"])}
root = Path(os.environ["RUNNER_TEMP"]) / "panels-publication"
inputs = root / "inputs"
inputs.mkdir(parents=True)
request = {"source_root": os.environ["GITHUB_WORKSPACE"], "output_directory": str(root / "receipt")}
for key, fields in {
        "app": ("linux_artifact_zip", "artifact_id"),
        "build": ("linux_build_artifact_zip", "linux_build_artifact_id"),
        "mac": ("mac_artifact_zip", "mac_artifact_id"),
        "qualified-mac": ("qualified_mac_artifact_zip", "qualified_mac_artifact_id"),
        "linux-executor": ("linux_executor_artifact_zip", "linux_executor_artifact_id"),
        "mac-home": ("mac_home_artifact_zip", "mac_home_artifact_id"),
        "mac-home-build": ("mac_home_build_artifact_zip", "mac_home_build_artifact_id"),
        "machine-compatibility-darwin": ("machine_compatibility_darwin_zip", "machine_compatibility_darwin_artifact_id"),
        "machine-compatibility-linux": ("machine_compatibility_linux_zip", "machine_compatibility_linux_artifact_id"),
}.items():
    target = inputs / f"{key}.zip"
    with target.open("xb") as output:
        subprocess.run(["gh", "api", f"repos/{os.environ['GITHUB_REPOSITORY']}/actions/artifacts/{ids[key]}/zip"],
                       stdout=output, check=True)
    request.update(zip(fields, (str(target), str(ids[key]))))
request.update(mac_build_result="success", linux_executor_build_result="success", mac_home_build_result="success")
(root / "receipt-request.json").write_text(json.dumps(request))
