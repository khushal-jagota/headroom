"""Disposable filesystem proofs for prevention and final-session evidence."""

from __future__ import annotations

import os
import pwd
import re
import subprocess
import sys
from pathlib import Path

import pytest
from tests.support.skill_tree_protection.inventory import (
    capture_launch_inventory,
    compare_manifests,
    snapshot,
)

REPO = Path(__file__).resolve().parents[2]


def _child_env(home: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PLAN_", "PANELS_REAL_"))
        and key not in {"CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERMES_HOME"}
    }
    env.update(
        HOME=str(home),
        PLAN_DB_PATH=str(home / "data/planner.db"),
        PYTHONPATH=str(REPO),
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
    )
    return env


def test_launch_inventory_freezes_aliases_and_manifest_detects_persistent_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = tmp_path / "account"
    initial = tmp_path / "initial"
    configured = tmp_path / "configured-outside-home"
    named_home = tmp_path / "named-user-home"
    monkeypatch.setattr(
        pwd, "getpwnam",
        lambda username: pwd.struct_passwd((username, "x", 1234, 1234, "", str(named_home), "")),
    )
    skills = initial / ".claude/skills"
    skills.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    content = external / "SKILL.md"
    content.write_text("original")
    (skills / "shared").symlink_to(external, target_is_directory=True)
    (external / "cycle").symlink_to(skills, target_is_directory=True)
    (skills / "broken").symlink_to(tmp_path / "absent")
    env = {
        "HOME": str(initial),
        "CODEX_HOME": str(configured),
        "PLAN_HERMES_HOME": str(tmp_path / "hermes-one"),
        "HERMES_HOME": str(tmp_path / "hermes-two"),
        "PLAN_DB_PATH": str(tmp_path / "database/planner.db"),
        "CLAUDE_CONFIG_DIR": "~skill-test-user/profile",
    }
    inventory, baseline = capture_launch_inventory(env, tmp_path, account)
    for root in (
        account / ".claude/skills", initial / ".claude/skills",
        configured / "skills", tmp_path / "hermes-one/skills",
        tmp_path / "hermes-two/skills", tmp_path / "database/skills",
        named_home / "profile/skills",
    ):
        assert str(root) in inventory.roots
    assert str(external) in inventory.protected_paths
    assert str(tmp_path / "absent") in inventory.protected_paths
    assert (str(skills / "shared"), str(external)) in inventory.symlink_targets
    assert any(entry.kind == "missing" for entry in baseline.entries)
    assert baseline.metrics.regular_files >= 1
    assert baseline.metrics.hashed_bytes >= len("original")
    env["CODEX_HOME"] = str(tmp_path / "later")
    assert str(tmp_path / "later/skills") not in inventory.roots
    assert not compare_manifests(baseline, snapshot(inventory))
    content.write_text("changed bytes")
    (skills / "broken").unlink()
    (skills / "broken").symlink_to(tmp_path / "different-absent")
    (skills / "new").write_text("new")
    differences = compare_manifests(baseline, snapshot(inventory))
    changed_paths = {difference.path for difference in differences}
    assert str(content) in changed_paths
    assert str(skills / "broken") in changed_paths
    assert str(skills / "new") in changed_paths
    assert baseline.metrics.seconds >= 0



def test_snapshot_freezes_external_graph_and_rejects_special_files(tmp_path: Path) -> None:
    home = tmp_path / "home"
    skills = home / ".claude/skills"
    skills.mkdir(parents=True)
    removed = skills / "removed"
    removed.write_bytes(b"original")
    changed_type = skills / "changed-type"
    changed_type.write_bytes(b"original")
    inventory, baseline = capture_launch_inventory({"HOME": str(home)}, tmp_path, home)
    outside = tmp_path / "new-external"
    outside.mkdir()
    os.mkfifo(outside / "must-not-read")
    (skills / "new-link").symlink_to(outside, target_is_directory=True)
    removed.unlink()
    changed_type.unlink()
    changed_type.mkdir()
    # A new link changes evidence but must not expand traversal into account files.
    differences = {entry.path: entry.change for entry in compare_manifests(
        baseline, snapshot(inventory)
    )}
    assert differences[str(removed)] == "removed"
    assert differences[str(changed_type)] == "changed"
    assert differences[str(skills / "new-link")] == "added"
    assert str(outside / "must-not-read") not in differences
    os.mkfifo(skills / "unsupported")
    with pytest.raises(RuntimeError, match="unsupported"):
        snapshot(inventory)
    (skills / "unsupported").unlink()
    # An owner or unaudited child can redirect an ancestor during the session.
    # Never descend into the newly reached target, even when it has special files.
    skills.parent.rename(home / ".claude-old")
    (outside / "skills").mkdir()
    os.mkfifo(outside / "skills/must-not-read")
    (home / ".claude").symlink_to(outside, target_is_directory=True)
    redirected = snapshot(inventory)
    assert any(entry.kind == "redirected" for entry in redirected.entries)
    assert not any(entry.path == str(outside / "skills/must-not-read")
                   for entry in redirected.entries)
    assert compare_manifests(baseline, redirected)

def test_real_audited_operations_refuse_before_mutation(tmp_path: Path) -> None:
    # An irreversible hook belongs in a child, never in the enclosing pytest session.
    script = r'''
import os
from pathlib import Path
import sys
from tests.support.skill_tree_protection.audit import AuditGuard
from tests.support.skill_tree_protection.contracts import GuardRefusal, ProtectionInventory
from tests.support.skill_tree_protection.inventory import snapshot, compare_manifests
from planner.skill_sources import provision_native_backend_skills
root = Path(sys.argv[1])
profile = root / "configured-outside-home"
skills = profile / "skills"
skills.mkdir(parents=True)
file = skills / "SKILL.md"
file.write_text("unchanged")
empty = skills / "empty"
empty.mkdir()
literal_suffix = skills / "literal (deleted)"
literal_suffix.mkdir()
(literal_suffix / "SKILL.md").write_text("literal suffix")
(literal_suffix / "empty").mkdir()
literal_suffix_fd = os.open(literal_suffix, os.O_RDONLY)
assert os.readlink(f"/proc/self/fd/{literal_suffix_fd}").endswith(" (deleted)")
assert os.fstat(literal_suffix_fd).st_nlink > 0
(root / "parent-alias").symlink_to(empty, target_is_directory=True)
default = root / "home/.claude/skills"
default.mkdir(parents=True)
(default / "SKILL.md").write_text("default")
external = root / "external"
external.mkdir()
(external / "SKILL.md").write_text("external")
(skills / "linked").symlink_to(external, target_is_directory=True)
(skills / "dangling").symlink_to(root / "absent")
alias = root / "alias"
alias.symlink_to(skills, target_is_directory=True)
source = root / "source"
source.write_text("source")
database = root / "database"
(database / "skills/panels").mkdir(parents=True)
(database / "skills/panels/SKILL.md").write_text("managed")
fd = os.open(skills, os.O_RDONLY)
unknown_fd = os.open(root, os.O_RDONLY)
detached = root / "detached"
detached.mkdir()
detached_fd = os.open(detached, os.O_RDONLY)
detached.rmdir()
assert os.fstat(detached_fd).st_nlink == 0
# The old pathname now names a different, protected tree. Never reconstruct a
# deleted descriptor's path by stripping the procfs marker.
detached.mkdir()
(detached / "SKILL.md").write_text("replacement")
(detached / "empty").mkdir()
inventory = ProtectionInventory(
    (str(skills), str(default), str(detached)),
    (str(skills), str(default), str(external), str(detached)),
)
baseline = snapshot(inventory)
guard = AuditGuard(inventory)
guard.install()
guard.set_context("test_guard::sentinel")
operations = [
    lambda: (default / "SKILL.md").write_text("bad"),
    lambda: file.write_text("bad"),
    lambda: os.open(str(file), os.O_RDONLY | os.O_CREAT),
    lambda: (alias / "SKILL.md").write_bytes(b"bad"),
    lambda: (root / "parent-alias/../SKILL.md").write_text("bad"),
    lambda: (skills / "linked/SKILL.md").write_text("bad"),
    lambda: os.truncate(file, 0),
    lambda: file.unlink(),
    lambda: (skills / "dangling").unlink(),
    lambda: (skills / "new").mkdir(),
    lambda: empty.rmdir(),
    lambda: profile.rename(root / "moved-profile"),
    lambda: os.rmdir(profile),
    lambda: os.rename(source, skills / "replacement"),
    lambda: os.rename(file, root / "moved-file"),
    lambda: os.link(file, root / "hard-alias"),
    lambda: os.link(source, skills / "hard-entry"),
    lambda: os.symlink(source, skills / "new-link"),
    lambda: os.symlink(file, root / "new-alias"),
    lambda: os.remove("SKILL.md", dir_fd=fd),
    lambda: os.rmdir("empty", dir_fd=fd),
    # A literal suffix on a live directory cannot spoof deleted-state evidence.
    lambda: os.remove("SKILL.md", dir_fd=literal_suffix_fd),
    lambda: os.rmdir("empty", dir_fd=literal_suffix_fd),
    lambda: os.remove("unknown", dir_fd=999999),
    lambda: os.remove("../configured-outside-home/skills/SKILL.md", dir_fd=detached_fd),
    lambda: os.rmdir("../configured-outside-home", dir_fd=detached_fd),
    lambda: os.remove(file, dir_fd=detached_fd),
    lambda: os.rmdir(".", dir_fd=detached_fd),
    lambda: os.rmdir("..", dir_fd=detached_fd),
    lambda: os.rename("SKILL.md", "renamed", src_dir_fd=fd, dst_dir_fd=fd),
    lambda: os.open("SKILL.md", os.O_WRONLY, dir_fd=fd),
    lambda: os.open("relative-unknown", os.O_CREAT | os.O_WRONLY, dir_fd=unknown_fd),
    lambda: provision_native_backend_skills(profile, database),
]
for number, operation in enumerate(operations):
    try:
        operation()
    except GuardRefusal:
        pass
    else:
        raise AssertionError(f"operation {number} escaped guard")
assert len(guard.receipts) == len(operations)
assert all(receipt.node == "test_guard::sentinel" for receipt in guard.receipts)
assert not compare_manifests(baseline, snapshot(inventory))
for operation in (
    lambda: os.remove("SKILL.md", dir_fd=detached_fd),
    lambda: os.rmdir(b"empty", dir_fd=detached_fd),
):
    before = snapshot(inventory)
    try:
        operation()
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("deleted directory leaf removal unexpectedly succeeded")
    assert not compare_manifests(before, snapshot(inventory))
assert len(guard.receipts) == len(operations)
assert file.read_text() == "unchanged"
assert source.read_text() == "source"
assert not (root / "moved-profile").exists()
# Absolute private opens and ordinary built-in relative opens remain usable.
os.close(os.open(str(root / "allowed"), os.O_CREAT | os.O_WRONLY))
(skills / "../outside-skills").write_text("allowed")
read_fd, write_fd = os.pipe()
with os.fdopen(write_fd, "w") as stream:
    stream.write("pipe")
with os.fdopen(read_fd) as stream:
    assert stream.read() == "pipe"
os.chdir(root)
with open("builtin-relative", "w") as stream:
    stream.write("allowed")
os.close(fd)
os.close(unknown_fd)
os.close(literal_suffix_fd)
os.close(detached_fd)
# Registration can be silently suppressed by an earlier hook. Acknowledge installation.
def suppress(event, args):
    if event == "sys.addaudithook":
        raise RuntimeError("registration suppressed")
sys.addaudithook(suppress)
try:
    AuditGuard(inventory).install()
except RuntimeError:
    pass
else:
    raise AssertionError("suppressed hook installation passed")
print("all mutation probes refused before execution")
'''
    child = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env=_child_env(tmp_path / "home"), cwd=REPO,
        text=True, capture_output=True, timeout=30,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    assert "all mutation probes refused" in child.stdout


def _pytest_probe(tmp_path: Path, body: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    home = tmp_path / "launch-home"
    skills = home / ".claude/skills"
    skills.mkdir(parents=True)
    sentinel = skills / "SKILL.md"
    sentinel.write_text("original")
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "conftest.py").write_text(
        "from tests.support.skill_tree_protection.pytest_plugin import bootstrap\n"
        "protection = bootstrap()\n"
        "def pytest_configure(config):\n"
        "    config.pluginmanager.register(protection, 'probe-protection')\n"
    )
    (suite / "test_probe.py").write_text(body.replace("SENTINEL", repr(str(sentinel))))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-s", "--confcutdir", str(suite), str(suite)],
        cwd=suite, env=_child_env(home), text=True, capture_output=True, timeout=30,
    )
    return result, sentinel


@pytest.mark.parametrize("unrelated_failure", [False, True])
def test_caught_refusal_poison_session_and_names_path_operation_node(
    tmp_path: Path, unrelated_failure: bool,
) -> None:
    result, sentinel = _pytest_probe(
        tmp_path,
        "import os, pytest\n"
        "from pathlib import Path\n"
        "def refuse():\n"
        "    try:\n        Path(SENTINEL).write_text('bad')\n"
        "    except PermissionError:\n        pass\n"
        "@pytest.fixture\n"
        "def phases():\n"
        "    refuse()\n    yield\n    refuse()\n"
        "@pytest.mark.real_provider_homes\n"
        "def test_caught(phases):\n"
        "    assert Path(os.environ['HOME']) == Path(SENTINEL).parents[2]\n"
        "    refuse()\n" + (
            "    assert False, 'unrelated failure'\n" if unrelated_failure else ""
        ),
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert str(sentinel) in output
    assert "open" in output
    assert "test_probe.py::test_caught" in output
    assert "Skill-tree guard receipts (3)" in output
    if unrelated_failure:
        assert output.index("path=") < output.index("FAILURES")
    assert sentinel.read_text() == "original"


def test_collection_refusal_is_recorded_before_tests(tmp_path: Path) -> None:
    result, sentinel = _pytest_probe(
        tmp_path, "from pathlib import Path\nPath(SENTINEL).write_text('bad')\n",
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert str(sentinel) in output
    assert "open" in output
    assert "collection" in output.lower()
    assert sentinel.read_text() == "original"


def test_child_bypass_reports_unknown_source_even_when_test_already_fails(tmp_path: Path) -> None:
    result, sentinel = _pytest_probe(
        tmp_path,
        "import os, pytest, subprocess, sys\n"
        "from pathlib import Path\n"
        "@pytest.mark.real_provider_homes\n"
        "def test_child():\n"
        "    assert Path(os.environ['HOME']) == Path(SENTINEL).parents[2]\n"
        "    subprocess.run([sys.executable, '-c', "
        '"from pathlib import Path; Path(" + repr(SENTINEL) + ").write_text(\'child\')"], '
        "check=True)\n"
        "    assert False, 'independent test failure'\n",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert sentinel.read_text() == "child"
    assert str(sentinel) in output
    assert "independent test failure" in output
    assert "unknown" in output.lower()
    assert "owner change" in output.lower()
    assert "receipt" in output.lower()


def test_home_routing_precedes_all_fixture_scopes_and_preserves_teardown_paths(
    tmp_path: Path,
) -> None:
    # Module/session setup can launch native processes before function fixtures exist.
    result, sentinel = _pytest_probe(
        tmp_path,
        "import os, pytest, shutil\n"
        "from pathlib import Path\n"
        "from conftest import protection\n"
        "from tests.support.skill_tree_protection.inventory import snapshot, compare_manifests\n"
        "keys = ['HOME', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'PLAN_HERMES_HOME', 'HERMES_HOME']\n"
        "def assert_protected_unchanged(label, before):\n"
        "    after = snapshot(protection.inventory)\n"
        "    assert not compare_manifests(before, after)\n"
        "    def metrics(manifest):\n"
        "        return (manifest.metrics.entries, manifest.metrics.regular_files, "
        "manifest.metrics.hashed_bytes)\n"
        "    print(label, 'before=', metrics(before), 'after=', metrics(after))\n"
        "def private_homes():\n"
        "    homes = {key: Path(os.environ[key]) for key in keys}\n"
        "    assert homes['HOME'] != Path(SENTINEL).parents[2]\n"
        "    assert all(path.is_dir() for path in homes.values())\n"
        "    assert all(path.is_relative_to(homes['HOME']) for path in homes.values())\n"
        "    return homes\n"
        "@pytest.fixture(scope='session')\n"
        "def session_homes():\n"
        "    homes = private_homes()\n"
        "    cleanup_root = homes['HOME'] / 'cleanup-original'\n"
        "    cleanup_root.mkdir()\n"
        "    (cleanup_root / 'live-child').mkdir()\n"
        "    (cleanup_root / 'live-child/file').write_text('ordinary private cleanup')\n"
        "    detached = cleanup_root / 'detached'\n"
        "    detached.mkdir()\n"
        "    detached_fd = os.open(detached, os.O_RDONLY)\n"
        "    detached.rmdir()\n"
        "    yield homes\n"
        "    try:\n"
        "        assert all(path.is_dir() for path in homes.values())\n"
        "        before = snapshot(protection.inventory)\n"
        "        cleanup_root.rename(cleanup_root.with_name('cleanup-renamed'))\n"
        "        assert_protected_unchanged('ordinary temp-root rename', before)\n"
        "        before = snapshot(protection.inventory)\n"
        "        with pytest.raises(FileNotFoundError):\n"
        "            os.rmdir('missing-child', dir_fd=detached_fd)\n"
        "        assert_protected_unchanged('detached-descriptor teardown', before)\n"
        "        before = snapshot(protection.inventory)\n"
        "        assert shutil.rmtree.avoids_symlink_attacks\n"
        "        shutil.rmtree(cleanup_root.with_name('cleanup-renamed'))\n"
        "        assert_protected_unchanged('descriptor-relative tree removal', before)\n"
        "    finally:\n"
        "        os.close(detached_fd)\n"
        "@pytest.fixture(scope='module')\n"
        "def module_homes(session_homes):\n"
        "    homes = private_homes()\n"
        "    assert homes == session_homes\n"
        "    yield homes\n"
        "    assert all(path.is_dir() for path in homes.values())\n"
        "def test_first(module_homes, tmp_path):\n"
        "    homes = private_homes()\n"
        "    assert homes == module_homes\n"
        "    assert list(tmp_path.iterdir()) == []\n"
        "def test_second(module_homes):\n"
        "    homes = private_homes()\n"
        "    assert all(path.is_dir() for path in module_homes.values())\n",
    )
    output = result.stdout + result.stderr
    print(output)
    assert result.returncode == 0, output
    assert "2 passed" in output
    assert "Skill-tree guard receipts (0): []" in output
    manifest_metrics = re.findall(
        r"Skill-tree (?:baseline|final): seconds=\S+ "
        r"(entries=\d+ regular_files=\d+ hashed_bytes=\d+)",
        output,
    )
    assert len(manifest_metrics) == 2, output
    assert manifest_metrics[0] == manifest_metrics[1], output
    assert "Skill-tree manifest differences:" not in output
    assert sentinel.read_text() == "original"
