"""Real offline npm proof; this does not qualify a Mac or run a provider."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from planner.environments.resident.native_builder import (
    _environment,
    _npm_environment,
    run_native_command,
)


def test_real_npm_ci_uses_distinct_empty_configs_and_excludes_ambient_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Protect npm's real config-loader boundary beyond the scripted builder tests."""
    node_path, npm_path = shutil.which("node"), shutil.which("npm")
    assert node_path is not None, "real Node is required for npm configuration verification"
    assert npm_path is not None, "real npm is required for npm configuration verification"
    node, npm = Path(node_path).resolve(), Path(npm_path).resolve()
    assert node.is_file() and npm.is_file() and npm.suffix == ".js"
    home = tmp_path / "resident account"
    home.mkdir()
    hostile_registry = "https://sentinel.invalid/credential-registry/"
    mixed_case_registry = "https://mixed-case-sentinel.invalid/credential-registry/"
    hostile_cache = tmp_path / "hostile cache"
    hostile_config = tmp_path / "ambient npm config"
    hostile_config.write_text(f"registry={hostile_registry}\n")
    (home / ".npmrc").write_text(f"registry={hostile_registry}\ncache={hostile_cache}\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NPM_CONFIG_REGISTRY", hostile_registry)
    monkeypatch.setenv("NpM_CoNfIg_Registry", mixed_case_registry)
    monkeypatch.setenv("npm_config_cache", str(hostile_cache))
    monkeypatch.setenv("NPM_CONFIG_USERCONFIG", str(hostile_config))
    monkeypatch.setenv("npm_config_globalconfig", str(hostile_config))
    monkeypatch.setenv("NODE_OPTIONS", f"--require={tmp_path / 'missing preload.js'}")
    monkeypatch.setenv("NODE_PATH", str(tmp_path / "ambient node modules"))
    package = tmp_path / "empty locked backend"
    package.mkdir()
    package_definition = {"name": "resident-npm-config-proof", "version": "1.0.0"}
    (package / "package.json").write_text(json.dumps(package_definition))
    (package / "package-lock.json").write_text(
        json.dumps(
            {
                **package_definition,
                "lockfileVersion": 3,
                "requires": True,
                "packages": {"": package_definition},
            }
        )
    )
    lock_bytes = (package / "package-lock.json").read_bytes()
    started = time.monotonic()
    # The product uses these exact helpers under the same temporary scope. No
    # npm overrides are added here: npm ci has the production argv and no deps.
    with tempfile.TemporaryDirectory(prefix="panels-wheel-", dir=tmp_path) as temporary:
        environment = _npm_environment(Path(temporary), _environment())
        user = Path(environment["NPM_CONFIG_USERCONFIG"])
        global_config = Path(environment["NPM_CONFIG_GLOBALCONFIG"])
        assert user.is_absolute() and global_config.is_absolute()
        assert user != global_config and not user.samefile(global_config)
        for config in (user, global_config):
            assert config.parent == Path(temporary)
            assert config.is_file() and not config.is_symlink() and config.read_bytes() == b""
        assert environment["HOME"] == str(home)
        assert not any(key in environment for key in ("NODE_OPTIONS", "NODE_PATH"))
        assert {key for key in environment if key.lower().startswith("npm_config_")} == {
            "NPM_CONFIG_USERCONFIG",
            "NPM_CONFIG_GLOBALCONFIG",
        }
        node_identity = json.loads(
            run_native_command(
                [str(node), "--eval", "console.log(JSON.stringify(process.versions))"],
                package,
                environment,
                10,
            )
        )
        npm_version = run_native_command(
            [str(node), str(npm), "--version"], package, environment, 10
        ).strip()
        effective = json.loads(
            run_native_command(
                [str(node), str(npm), "config", "list", "--json"], package, environment, 10
            )
        )
        assert effective["userconfig"] == str(user)
        assert effective["globalconfig"] == str(global_config)
        assert effective["registry"] not in {hostile_registry, mixed_case_registry}
        assert effective["cache"] != str(hostile_cache)
        run_native_command(
            [str(node), str(npm), "ci", "--no-audit", "--no-fund"], package, environment, 10
        )
        assert (package / "package-lock.json").read_bytes() == lock_bytes
        assert not list((package / "node_modules").glob("*"))
        assert user.read_bytes() == global_config.read_bytes() == b""
    assert not user.exists() and not global_config.exists()
    assert not hostile_cache.exists()
    print(
        json.dumps(
            {
                "node": str(node),
                "node_version": node_identity["node"],
                "npm": str(npm),
                "npm_version": npm_version,
                "subprocess_seconds": round(time.monotonic() - started, 3),
            },
            sort_keys=True,
        )
    )
