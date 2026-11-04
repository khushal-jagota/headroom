"""Storage proofs use disposable trees; no fixture touches the deployment.

These checks cover alias classes absent from identity/pin tests: nested symlinks,
hardlinks, mount backing identity, missing evidence, Python redirects, and the
important independent virtualenv/system-interpreter case. One real namespace
case crosses the kernel mount-evidence boundary; it must fail if unavailable.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from development_safety import scanner
from development_safety.contracts import SafetyRefusal


def _fixture(tmp_path: Path) -> tuple[Path, Path, tuple[scanner.Mount, ...]]:
    if sys.platform == "darwin":
        pytest.skip("Linux dependency storage proof requires /proc/self/mountinfo and Bubblewrap")
    protected = tmp_path / "deployed"
    protected.mkdir()
    independent = tmp_path / "independent"
    independent.mkdir()
    mounts = scanner.parse_mountinfo(Path("/proc/self/mountinfo").read_text())
    return protected, independent, mounts


def test_storage_aliases_and_independent_hardlinks(tmp_path: Path) -> None:
    protected, independent, mounts = _fixture(tmp_path)
    source = protected / "package.py"
    source.write_text("deployed")
    scanner._check_paths([independent / "new-dependencies"], [protected], mounts)
    # Independent hardlinks are valid: a link count alone is not sharing proof.
    local = independent / "local"
    local.write_text("local")
    os.link(local, independent / "local-alias")
    scanner._check_paths([independent], [protected], mounts)
    os.link(source, independent / "shared")
    with pytest.raises(SafetyRefusal, match="filesystem identity") as refusal:
        scanner._check_paths([independent], [protected], mounts)
    assert refusal.value.diagnostic.classification == "shared deployment storage detected"
    (independent / "shared").unlink()
    nested = independent / "nested"
    nested.mkdir()
    (nested / "indirect").symlink_to(Path("../../deployed"))
    with pytest.raises(SafetyRefusal, match="path ancestry"):
        scanner._check_paths([independent], [protected], mounts)


def test_deployed_external_dependency_roots_remain_protected(tmp_path: Path) -> None:
    protected, independent, mounts = _fixture(tmp_path)
    shared = tmp_path / "external-dependencies"
    shared.mkdir()
    for relative in ("app/web/node_modules", "app/.venv"):
        link = protected / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(shared, target_is_directory=True)
    (independent / "node_modules").symlink_to(shared, target_is_directory=True)
    with pytest.raises(SafetyRefusal, match="shared deployment storage detected"):
        scanner._check_paths([independent], [protected], mounts)


def test_nested_deployed_dependency_links_protect_directory_and_file_targets(
    tmp_path: Path,
) -> None:
    protected, independent, mounts = _fixture(tmp_path)
    deployed_modules = protected / "app/web/node_modules"
    candidate_modules = independent / "web/node_modules"
    deployed_modules.mkdir(parents=True)
    candidate_modules.mkdir(parents=True)
    external_package = tmp_path / "external-package"
    external_package.mkdir()
    external_file = tmp_path / "external-package.py"
    external_file.write_text("fixture")
    (deployed_modules / "package").symlink_to(external_package, target_is_directory=True)
    (candidate_modules / "package").symlink_to(external_package, target_is_directory=True)
    with pytest.raises(SafetyRefusal, match="shared deployment storage detected"):
        scanner._check_paths([candidate_modules], [protected], mounts)
    (candidate_modules / "package").unlink()
    (deployed_modules / "package.py").symlink_to(external_file)
    (candidate_modules / "package.py").symlink_to(external_file)
    with pytest.raises(SafetyRefusal, match="shared deployment storage detected"):
        scanner._check_paths([candidate_modules], [protected], mounts)
    # The underlying file itself is also deployed storage even when accessed
    # directly rather than through either symlink spelling.
    with pytest.raises(SafetyRefusal, match="shared deployment storage detected"):
        scanner._check_paths([external_file], [protected], mounts)


def test_deployed_path_only_pth_owns_external_lookup_storage(tmp_path: Path) -> None:
    protected, independent, mounts = _fixture(tmp_path)
    site = protected / "app/.venv/lib/python3.12/site-packages"
    site.mkdir(parents=True)
    external = tmp_path / "editable-source"
    external.mkdir()
    vendor = tmp_path / "external-vendor"
    vendor.mkdir()
    (external / "vendor").symlink_to(vendor, target_is_directory=True)
    # Linux venvs may also expose lib through lib64. Site discovery must be
    # based on directory identity whichever alias traversal visits first.
    (protected / "app/.venv/lib64").symlink_to("lib", target_is_directory=True)
    (site / "compat.pth").write_text(str(external) + "\n")
    # A self-reference proves discovery terminates rather than reprocessing the
    # same package directory forever through a redirect.
    (site / "self.pth").write_text(str(site) + "\n")
    (site / "distutils-precedence.pth").write_text(scanner._SETUPTOOLS_HOOK + "\n")
    with pytest.raises(SafetyRefusal, match="shared deployment storage detected"):
        scanner._check_paths([external], [protected], mounts)
    with pytest.raises(SafetyRefusal, match="shared deployment storage detected"):
        scanner._check_paths([vendor], [protected], mounts)
    scanner._check_paths([independent], [protected], mounts)
    (site / "unsupported.pth").write_text("import uninspectable_lookup\n")
    with pytest.raises(SafetyRefusal, match="executable Python redirect"):
        scanner._check_paths([independent], [protected], mounts)


def test_mount_decode_longest_mapping_and_nested_alias(tmp_path: Path) -> None:
    protected, independent, _ = _fixture(tmp_path)
    alias = independent / "space alias"
    alias.mkdir()
    escaped = str(alias).replace(" ", r"\040")
    text = f"1 0 8:1 / / rw - ext4 disk rw\n2 1 8:1 {protected} {escaped} rw - ext4 disk rw\n"
    mounts = scanner.parse_mountinfo(text)
    with pytest.raises(SafetyRefusal, match="mount mapping") as refusal:
        scanner._check_paths([independent], [protected], mounts)
    assert f"backing {protected}" in str(refusal.value)
    assert str(alias) in str(refusal.value)


def test_stacked_mounts_use_visible_member(tmp_path: Path) -> None:
    """systemd PrivateTmp stacks two /tmp mounts; longest point alone is ambiguous."""
    mounts = scanner.parse_mountinfo(
        "1 0 8:1 / / rw - ext4 disk rw\n"
        "2 1 0:9 / /tmp rw - tmpfs tmp rw\n"
        "3 2 0:9 /private/tmp /tmp rw - tmpfs tmp rw\n"
    )
    mount, backing = scanner._mapping(Path("/tmp/dependencies"), mounts)
    assert mount.mount_id == 3
    assert backing == Path("/private/tmp/dependencies")


def test_missing_or_ambiguous_evidence_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protected, independent, mounts = _fixture(tmp_path)
    (independent / "dangling").symlink_to(tmp_path / "absent")
    with pytest.raises(SafetyRefusal, match="cannot establish dependency safety"):
        scanner._check_paths([independent], [protected], mounts)
    (independent / "dangling").unlink()
    with pytest.raises(SafetyRefusal, match="mount information"):
        scanner._check_paths([independent], [], [])
    monkeypatch.setattr(
        scanner,
        "parse_mountinfo",
        lambda text: (_ for _ in ()).throw(ValueError("unreadable mount evidence")),
    )
    with pytest.raises(SafetyRefusal, match="unreadable mount evidence"):
        scanner.check_paths([independent], environment={})


def test_normal_root_cannot_be_replaced_by_environment(tmp_path: Path) -> None:
    roots = scanner.protected_roots(
        {"HOME": str(tmp_path), "PANELS_CURRENT_ROOT": str(tmp_path / "additional")}
    )
    import pwd

    assert roots[0] == Path(pwd.getpwuid(os.getuid()).pw_dir) / "Deployments/Panels/current"
    assert roots[1] == tmp_path / "additional"


def test_python_redirects_and_shared_system_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protected, independent, _ = _fixture(tmp_path)
    system = Path(sys.executable).resolve()
    deployed_venv = protected / "app/.venv"
    for root in (deployed_venv, independent):
        (root / "bin").mkdir(parents=True)
        (root / "bin/python").symlink_to(system)
        (root / "lib/python3.12/site-packages").mkdir(parents=True)
        (root / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    monkeypatch.setattr(scanner, "protected_roots", lambda environment: (protected,))
    scanner.check_python(independent, independent, environment={})
    site = independent / "lib/python3.12/site-packages"
    pth = site / "compat.pth"
    pth.write_text(str(independent) + "\n")
    (site / "distutils-precedence.pth").write_text(scanner._SETUPTOOLS_HOOK + "\n")
    scanner.check_python(independent, independent, environment={})
    pth.write_text(str(protected) + "\n")
    with pytest.raises(SafetyRefusal, match="shared deployment storage detected"):
        scanner.check_python(independent, independent, environment={})
    pth.write_text("import unsupported_redirect\n")
    with pytest.raises(SafetyRefusal, match="executable Python redirect"):
        scanner.check_python(independent, independent, environment={})
    with pytest.raises(SafetyRefusal, match="shared deployment storage detected") as refusal:
        scanner.check_python(independent, independent, environment={"PYTHONPATH": str(protected)})
    assert refusal.value.diagnostic.examined == protected
    assert refusal.value.diagnostic.signal == "path ancestry"
    assert "unset PYTHONPATH" in str(refusal.value)
    assert "safety check still runs" in str(refusal.value)
    with pytest.raises(SafetyRefusal, match="unset PYTHONHOME"):
        scanner.check_python_lookup(independent, environment={"PYTHONHOME": str(independent)})


def test_real_disposable_bind_mount_namespace(tmp_path: Path) -> None:
    protected, independent, _ = _fixture(tmp_path)
    alias = independent / "alias"
    alias.mkdir()
    (protected / "sentinel").write_text("fixture only")
    checkout = Path(__file__).resolve().parents[2]
    program = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "scripts"))
from development_safety.scanner import _check_paths, parse_mountinfo
from development_safety.contracts import SafetyRefusal
try:
    mounts = parse_mountinfo(Path('/proc/self/mountinfo').read_text())
    _check_paths([Path(sys.argv[2])], [Path(sys.argv[3])], mounts)
except SafetyRefusal as error:
    print(error)
    assert error.diagnostic.classification == 'shared deployment storage detected'
else:
    raise AssertionError('real bind alias admitted')
"""
    result = subprocess.run(
        [
            "bwrap",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-net",
            "--die-with-parent",
            "--ro-bind",
            "/",
            "/",
            "--proc",
            "/proc",
            "--bind",
            str(protected),
            str(alias),
            "/usr/bin/python3",
            "-I",
            "-S",
            "-B",
            "-c",
            program,
            str(checkout),
            str(independent),
            str(protected),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"Bind proof incomplete (namespace unavailable or failed): {result.stderr}\n{result.stdout}"
    )
    assert "shared deployment storage detected" in result.stdout
