"""Prove shell admission with real suites, reports, and the deployment mount boundary.

Reporter unit matrices cannot prove that the workflow executes and consumes their
reports. This single disposable setup joins those commands without paid providers,
production data, or a second application browser host.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sysconfig
import time
import venv
from dataclasses import dataclass
from pathlib import Path

import playwright
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
# Capture the browser input before test setup replaces HOME with a private home.
_BROWSER_CACHE = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(
    Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright"
)
if _BROWSER_CACHE != "0":
    _BROWSER_CACHE = str(
        (Path(os.environ.get("INIT_CWD") or Path.cwd()) / _BROWSER_CACHE).resolve()
    )
SCENARIO_SELECTORS = (
    "CONVERSATION_SETTLED_CASE", "CONVERSATION_LATEST_CASE",
    "CONVERSATION_SCROLL_CASE", "PANELS_CONVERSATION_MENU_ONLY",
)
SUITES = (
    "pytest unit at requested source",
    "pytest integration at requested source",
    "pytest E2E at requested source",
    "Typechecked Vitest at requested source",
    "Browser hosts at requested source",
)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@dataclass
class CommandGate:
    source: Path
    gate: Path
    environment: dict[str, str]
    scripts: dict[str, str]
    selection: dict[str, object]
    browser_cache: Path

    def run(self, *names: str, fault: str = "") -> subprocess.CompletedProcess[str]:
        (self.gate / "selection.json").write_text(json.dumps(self.selection))
        admitted = self.gate / "admitted"
        admitted.unlink(missing_ok=True)
        # Separate shells retain the workflow's exit-on-error behavior for each
        # run block, including blocks that intentionally exit for no selection.
        command = "\n".join(
            "bash -euo pipefail -c " + shlex.quote(self.scripts[name]) for name in names
        )
        command += "\nprintf admitted > " + shlex.quote(str(admitted))
        result = subprocess.run(
            ["bwrap", "--bind", "/", "/", "--dev-bind", "/dev", "/dev",
             "--ro-bind", str(self.browser_cache), str(self.browser_cache), "--",
             "bash", "-euo", "pipefail", "-c", command],
            env=self.environment | {"GATE_FAULT": fault},
            text=True, capture_output=True, timeout=90,
        )
        assert admitted.exists() == (result.returncode == 0), result.stdout + result.stderr
        return result


@pytest.fixture(scope="module")
def command_gate(tmp_path_factory: pytest.TempPathFactory) -> CommandGate:
    temporary = tmp_path_factory.mktemp("deployment-commands")
    source, gate, shadow = (temporary / name for name in ("source", "gate", "protected"))
    reports = gate / "reports"
    for directory in (source, reports, gate / "artifact-shadow", shadow, temporary / "tools"):
        directory.mkdir(parents=True)
    browser_cache = (
        Path(playwright.__file__).resolve().parent / "driver/package/.local-browsers"
        if _BROWSER_CACHE == "0" else Path(_BROWSER_CACHE)
    ).resolve()
    if not browser_cache.is_dir():
        pytest.fail(f"Required preinstalled Playwright browser cache is absent: {browser_cache}")
    # A fresh interpreter environment, with the already installed pinned test
    # dependencies. Never copy or symlink an existing virtualenv.
    venv.EnvBuilder(with_pip=False).create(source / ".venv")
    executable = source / ".venv/bin/python"
    library = subprocess.check_output(
        [str(executable), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        text=True,
    ).strip()
    write(Path(library) / "gate-test-dependencies.pth", sysconfig.get_path("purelib") + "\n")
    write(source / ".venv/bin/pytest",
          f"#!{executable}\nfrom pytest import console_main\nraise SystemExit(console_main())\n")
    (source / ".venv/bin/pytest").chmod(0o755)
    pin = Path("planner/conversation/backends/codex_app_server/generate_bindings.py")
    write(source / "src" / pin, (ROOT / "src" / pin).read_text())
    for directory in (source / "src" / pin).parents:
        if directory == source / "src":
            break
        write(directory / "__init__.py", "")
    for layer in ("unit", "integration", "e2e"):
        write(source / f"tests/{layer}/test_gate.py", """
import os
from pathlib import Path
import pytest

def test_real_gate_case():
    assert not any(key.startswith(('PLAN_', 'PANELS_REAL_')) for key in os.environ)
    if os.environ.get('GATE_FAULT') == 'skip':
        pytest.skip('required local proof unavailable')
    Path(os.environ['GATE_PROTECTED_MARKER']).write_text('suite wrote here')
""")
    write(source / "tests/conftest.py", """
import os
from pathlib import Path
import pytest

@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_sessionfinish(session, exitstatus):
    yield
    report = Path(session.config.option.xmlpath)
    if os.environ.get('GATE_FAULT') == 'missing-report':
        report.unlink()
    if os.environ.get('GATE_FAULT') == 'invalid-report':
        report.write_text('not XML')
""")
    # Keep the real npm dependency guard local to the disposable requested source.
    fixture_scripts = source / "scripts"
    (fixture_scripts / "development_safety").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/dev", fixture_scripts / "dev")
    for name in ("__init__.py", "bootstrap.py", "contracts.py", "launcher.py", "scanner.py"):
        shutil.copy2(
            ROOT / "scripts/development_safety" / name,
            fixture_scripts / "development_safety" / name,
        )
    web = source / "web"
    # Keep copied runtime imports complete when these real helpers change.
    for name in ("package.json", "package-lock.json", "usage-aliases.mjs"):
        write(web / name, (ROOT / "web" / name).read_text())
    (web / "node_modules").symlink_to(ROOT / "web/node_modules", target_is_directory=True)
    write(web / "vitest.config.ts", """
import {defineConfig} from 'vitest/config';
export default defineConfig({test:{include:['tests/*.test.ts'],
  typecheck:{enabled:true,include:['tests/*.test.ts'],tsconfig:'./tsconfig.json'}}});
""")
    write(web / "tsconfig.json", json.dumps({"compilerOptions": {
        "target": "ES2022", "module": "ESNext", "moduleResolution": "bundler",
        "strict": True, "skipLibCheck": True, "noEmit": True,
    }, "include": ["tests/*.test.ts"]}))
    write(web / "tests/gate.test.ts", """
import {expect,test} from 'vitest';
test('real runtime proof',()=>{const value:number=2;expect(value+2).toBe(4)});
""")
    write(web / "build/markdown-worker.mjs",
          (ROOT / "web/build/markdown-worker.mjs").read_text())
    for name in ("browser/run.mjs", "browser/execute.py", "browser/contracts.mjs",
                 "support/scratch.mjs"):
        write(web / "tests" / name, (ROOT / "web/tests" / name).read_text())
    # Requested source predates selector admission. The control checkout must
    # enforce its rule without importing a new export from this candidate.
    contracts = web / "tests/browser/contracts.mjs"
    contracts.write_text('import assert from "node:assert/strict";\n\n'
                         + "export function selectedEntries" + contracts.read_text().split(
                             "export function selectedEntries", 1)[1])
    runner = web / "tests/browser/run.mjs"
    runner.write_text(
        "import {writeFileSync as markExecution} from 'node:fs';\n"
        "markExecution(process.env.GATE_RUNNER_MARKER, 'requested runner executed');\n"
        + runner.read_text().replace("assertCompleteCaseEnvironment, ", "").replace(
            "    assertCompleteCaseEnvironment(process.env);\n", "")
    )
    catalog = {"schema": 1, "entries": [{"id": "gate", "path": "web/tests/gate-browser.test.mjs",
                                          "cases": ["gate-case"]}]}
    write(web / "tests/browser/catalog.json", json.dumps(catalog))
    write(web / "tests/browser/cases/gate-case.mjs", """
import {writeFile} from 'node:fs/promises';
import {writeFileSync,unlinkSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
export async function prepare({scratchRoot}) {
  const path=join(scratchRoot,`gate-${process.pid}.html`);
  await writeFile(path,'<html><body><button>Real browser proof</button></body></html>');
  process.on('exit',()=>{
    const report=process.argv[process.argv.indexOf('--report')+1];
    if(process.env.GATE_FAULT==='invalid-browser') writeFileSync(report,'invalid JSON');
    if(process.env.GATE_FAULT==='missing-browser') unlinkSync(report);
  });
  const python=fileURLToPath(new URL('./gate-case.py',import.meta.url));
  return {inputs:[path],arguments:[path],python};
}
""")
    write(web / "tests/browser/cases/gate-case.py", """
import os
from pathlib import Path

def run(browser, urls):
    page = browser.new_page()
    page.goto(urls[0])
    button = page.get_by_role('button', name='Real browser proof')
    assert button.inner_text() == 'Real browser proof'
    Path(os.environ['GATE_PROTECTED_MARKER']).write_text('browser wrote here')
""")
    # Shared receipts must never fall back to this source entry on a parse error.
    write(web / "tests/gate-browser.test.mjs", """
import {writeFileSync} from 'node:fs';
import {run} from './browser/run.mjs';
writeFileSync(process.env.GATE_LEGACY_MARKER,'legacy route');
await run(['gate']);
""")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Gate proof", "-c",
                    "user.email=gate@example.invalid", "commit", "--allow-empty", "-qm", "fixture"],
                   check=True)
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True,
    ).strip()
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("PLAN_", "PANELS_REAL_"))
                   and key not in ("HERMES_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR",
                                   *SCENARIO_SELECTORS)}
    environment.update({
        "PANELS_CANDIDATE_SOURCE": str(source), "PANELS_GATE_DIR": str(gate),
        "PANELS_REPORT_DIR": str(reports), "PANELS_CODEX_TOOLS": str(temporary / "tools"),
        "PANELS_DEPLOY_VENV": str(source / ".venv"), "GITHUB_WORKSPACE": str(ROOT),
        "PANELS_REQUESTED_SHA": revision, "GITHUB_RUN_ID": "gate-proof", "GITHUB_RUN_ATTEMPT": "1",
        "PYTHONPATH": str(source / "src"), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PLAYWRIGHT_BROWSERS_PATH": _BROWSER_CACHE,
        "GATE_PROTECTED_MARKER": str(shadow / "marker"),
        "GATE_LEGACY_MARKER": str(gate / "legacy-route"),
        "GATE_RUNNER_MARKER": str(gate / "requested-runner"),
        # Deliberately poison these inputs: the workflow must scrub them itself.
        "PLAN_DB_PATH": "/never-use-production", "PANELS_REAL_CLAUDE_TESTS": "1",
    })
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
    steps = workflow["jobs"]["deploy"]["steps"]
    scripts = {step["name"]: step["run"].replace("/home/vps/Deployments/Panels", str(shadow))
               for step in steps if step.get("name") in SUITES}
    selection: dict[str, object] = {
        "unit": ["tests/unit/test_gate.py"], "integration": ["tests/integration/test_gate.py"],
        "e2e": ["tests/e2e/test_gate.py"], "vitest": ["web/tests/gate.test.ts"],
        "browser": ["web/tests/gate-browser.test.mjs"], "browser_format": "shared-v1",
    }
    return CommandGate(source, gate, environment, scripts, selection, browser_cache)


def test_actual_suite_commands_admit_real_proof_and_refuse_controlled_faults(
    command_gate: CommandGate,
) -> None:
    gate = command_gate
    started = time.monotonic()
    result = gate.run(*SUITES)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("unit.xml", "integration.xml", "e2e.xml", "vitest.json", "browser.json"):
        assert (gate.gate / "reports" / name).is_file()
    assert "Codex before pytest" in result.stderr
    assert "Required frontend proof:" in result.stdout
    assert "Required browser proof: 1 independent cases executed" in result.stdout
    assert Path(gate.environment["GATE_PROTECTED_MARKER"]).read_text() == "suite wrote here"
    assert (gate.gate / "artifact-shadow/marker").read_text() == "browser wrote here"
    assert not Path(gate.environment["GATE_LEGACY_MARKER"]).exists()
    assert Path(gate.environment["GATE_RUNNER_MARKER"]).exists()
    assert "assertCompleteCaseEnvironment" not in (
        gate.source / "web/tests/browser/contracts.mjs"
    ).read_text()

    # A prior passed receipt must not rescue selector refusal. Both routes stop
    # before requested JavaScript executes, including old shared source above.
    receipt_path = gate.gate / "reports/browser.json"
    passed_receipt = receipt_path.read_bytes()
    assert json.loads(passed_receipt)["outcome"] == "passed"
    for browser_format in ("shared-v1", "legacy"):
        gate.selection["browser_format"] = browser_format
        for key in SCENARIO_SELECTORS:
            for value in ("private-value", "0", " "):
                for marker in ("GATE_LEGACY_MARKER", "GATE_RUNNER_MARKER"):
                    Path(gate.environment[marker]).unlink(missing_ok=True)
                gate.environment[key] = value
                try:
                    result = gate.run(SUITES[-1])
                    assert result.returncode != 0, result.stdout + result.stderr
                    assert f"scenario selector: {key}" in result.stderr
                    assert "private-value" not in result.stderr
                    assert "ERR_MODULE_NOT_FOUND" not in result.stderr
                    assert not Path(gate.environment["GATE_LEGACY_MARKER"]).exists()
                    assert not Path(gate.environment["GATE_RUNNER_MARKER"]).exists()
                    assert receipt_path.read_bytes() == passed_receipt
                finally:
                    gate.environment.pop(key)
    gate.selection["browser_format"] = "shared-v1"
    selected_browser = gate.selection["browser"]
    gate.selection["browser"] = []
    gate.environment[SCENARIO_SELECTORS[0]] = "private-value"
    try:
        result = gate.run(SUITES[-1])
        assert result.returncode == 0, result.stdout + result.stderr
        assert not Path(gate.environment["GATE_RUNNER_MARKER"]).exists()
    finally:
        gate.selection["browser"] = selected_browser
        gate.environment.pop(SCENARIO_SELECTORS[0])

    for fault, expected in (
        ("missing-report", "No such file"),
        ("invalid-report", "syntax error"),
        ("skip", "unexpected required skip"),
    ):
        result = gate.run(SUITES[0], fault=fault)
        assert result.returncode != 0 and expected in result.stderr, result.stdout + result.stderr

    pytest_tool = gate.source / ".venv/bin/pytest"
    pytest_tool.rename(pytest_tool.with_suffix(".hidden"))
    try:
        result = gate.run(SUITES[0])
        assert result.returncode == 127 and "No such file" in result.stderr
    finally:
        pytest_tool.with_suffix(".hidden").rename(pytest_tool)

    selected = gate.source / "tests/unit/test_gate.py"
    content = selected.read_text()
    selected.write_text("# selected file intentionally collects zero tests\n")
    try:
        result = gate.run(SUITES[0])
        assert result.returncode == 5 and "no tests ran" in result.stdout
    finally:
        selected.write_text(content)

    for fault, expected in (("invalid-browser", "JSONDecodeError"),
                            ("missing-browser", "No such file")):
        result = gate.run(SUITES[-1], fault=fault)
        assert result.returncode != 0 and expected in result.stderr, result.stdout + result.stderr
        assert not Path(gate.environment["GATE_LEGACY_MARKER"]).exists()

    # Old requested source explicitly selects its original exit-code route.
    gate.selection["browser_format"] = "legacy"
    gate.environment.update(dict.fromkeys(SCENARIO_SELECTORS, ""))
    try:
        result = gate.run(SUITES[-1])
        assert result.returncode == 0, result.stdout + result.stderr
        assert Path(gate.environment["GATE_LEGACY_MARKER"]).read_text() == "legacy route"
    finally:
        gate.selection["browser_format"] = "shared-v1"
        for key in SCENARIO_SELECTORS:
            gate.environment.pop(key)
    print(f"Real deployment command proof: {time.monotonic() - started:.2f}s")
