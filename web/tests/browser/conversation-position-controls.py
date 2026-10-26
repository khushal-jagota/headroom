"""Reproduce historical defects using today's exact shared cases on old source.

Run with this checkout's Python, passing a new output directory. This is an explicit,
slow audit command, not another default browser entry. It creates its own disposable
worktree and fresh pinned venv, and removes both afterwards. Browser deps must already
be installed; matching Node locks allow reuse of this checkout's node_modules.
"""

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[3]
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(Path(__file__).with_suffix(".json").read_text())
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PLAN_", "PANELS_REAL_", "PANELS_BROWSER_", "PANELS_COMPANION_"))
        and key
        not in (
            "HERMES_HOME",
            "CODEX_HOME",
            "CLAUDE_CONFIG_DIR",
            "PYTHONPATH",
            "VIRTUAL_ENV",
            "CONVERSATION_SCROLL_CASE",
            "PANELS_TEST_PYTHON",
            "SCROLL_REGRESSION_EVIDENCE_DIR",
            "UNANSWERED_SEND_SCREENSHOT_DIR",
        )
    }
    fixture = Path(tempfile.mkdtemp(prefix="panels-conversation-controls-")) / "source"

    def command(arguments, log, cwd=fixture):
        with (output / log).open("w") as stream:
            result = subprocess.run(
                arguments, cwd=cwd, env=environment, stdout=stream, stderr=subprocess.STDOUT
            )
        return result.returncode

    subprocess.run(
        ["git", "worktree", "add", "--detach", str(fixture), manifest["source_revision"]],
        cwd=root,
        check=True,
    )
    try:
        # The old application is unchanged. Only test infrastructure/cases are overlaid.
        shutil.copytree(
            root / "web/tests/browser", fixture / "web/tests/browser", dirs_exist_ok=True
        )
        shutil.copy2(
            root / "web/tests/support/scratch.mjs", fixture / "web/tests/support/scratch.mjs"
        )
        for relative in ("web/package-lock.json", "agent_backends/package-lock.json"):
            assert (root / relative).read_bytes() == (fixture / relative).read_bytes(), relative
        for relative in ("web/node_modules", "agent_backends/node_modules"):
            (fixture / relative).symlink_to((root / relative).resolve(), target_is_directory=True)
        assert command([sys.executable, "-m", "venv", str(fixture / ".venv")], "venv.log") == 0
        python = str(fixture / ".venv/bin/python")
        environment["PANELS_TEST_PYTHON"] = python
        environment["SCROLL_REGRESSION_EVIDENCE_DIR"] = str(output / "screenshots")
        environment["PANELS_E2E_FAILURE_DIR"] = str(output / "browser-failures")
        assert (
            command([python, "-m", "pip", "install", "-r", "requirements.txt"], "dependencies.log")
            == 0
        )
        assert command([python, "-m", "pip", "install", "--editable", "."], "editable.log") == 0
        catalog = {
            "schema": 1,
            "entries": [
                {
                    "id": "conversation",
                    "path": "web/tests/conversation-browser.test.mjs",
                    "cases": ["conversation-pane"],
                    "companions": [],
                }
            ],
        }
        (fixture / "web/tests/browser/catalog.json").write_text(json.dumps(catalog))
        code = command(["node", "web/tests/browser/run.mjs", "conversation"], "unfixed-pane.log")
        text = (output / "unfixed-pane.log").read_text()
        outcomes = {}
        for status, name, diagnostic in re.findall(
            r"^SCROLL_CASE (PASS|FAIL) ([\w-]+)(?:: (.*))?$", text, re.M
        ):
            assert name not in outcomes, ("duplicate", name)
            outcomes[name] = {"status": status, "diagnostic": diagnostic}
        assert set(outcomes) == set(manifest["cases"]), outcomes
        assert code != 0, "The historical defect execution must refuse"
        for name, expected in manifest["cases"].items():
            actual = outcomes[name]
            assert actual["status"] == ("FAIL" if expected == "fails" else "PASS"), (name, actual)
            if expected != "fails":
                continue
            data = ast.literal_eval(actual["diagnostic"])
            if name == "draft-measurement":
                assert isinstance(data, list) and len(data) > 1
                displacement = max(row["scroll"] for row in data) - min(
                    row["scroll"] for row in data
                )
                assert displacement > 3, data
            elif name == "delayed-markdown-following":
                assert (
                    isinstance(data, dict)
                    and data["bottom"] > data["height"] + 3
                    and data["latest"] is False
                ), data
                displacement = data["bottom"] - data["height"]
            else:
                before, after = data
                assert after["connected"] and before["text"] == after["text"], data
                displacement = abs(after["top"] - before["top"])
                assert displacement > 3, data
            actual["observed_displacement_pixels"] = displacement
        proof = {
            "application_source_revision": manifest["source_revision"],
            "test_checkout_revision": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip(),
            "test_sha256": {
                relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
                for relative in (
                    "web/tests/browser/cases/conversation-pane.py",
                    "web/tests/browser/cases/conversation-pane.mjs",
                    "web/tests/browser/cases/conversation-composer.py",
                    "web/tests/browser/cases/conversation-composer.mjs",
                )
            },
            "test_overlay_is_not_pristine_historical_commit": True,
            "expected_nonzero_exit": code,
            "cases": outcomes,
            "composer_newline": manifest["composer_newline"],
        }
        (output / "controls.json").write_text(json.dumps(proof, indent=2) + "\n")
        print(json.dumps(proof, indent=2))
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(fixture)], cwd=root, check=True)
        fixture.parent.rmdir()


if __name__ == "__main__":
    main()
