# Independent implementation review

**Findings**

- **High: Tampered manifests are trusted for destructive paths and launch roots.**
  `src/planner/environments/materialize.py:382` reads manifest path fields directly from JSON, `_validate_registry_manifests` only checks duplicate/overlap among those supplied fields at `src/planner/environments/materialize.py:436`, and `remove` deletes `current.instance_root` at `src/planner/environments/materialize.py:180`. `run` also converts the manifest directly into a launch instance at `src/planner/environments/cli.py:465`, then uses `manifest.repository_roots` as the working directory. This violates the contract’s rejection of unsafe paths/repository roots and atomic manifest contract (`contract.md:47`, `contract.md:49`, `contract.md:67`, `contract.md:86`). A tampered preview manifest can point `instance_root` or `repository_roots` outside the environment root.
  **Smallest correction:** when reading any manifest, re-resolve the canonical instance from the requested/on-disk kind, id, environment root, port, credential reference, and repository roots; reject any payload path/account/kind/id/root mismatch. Revalidate repository roots on manifest read, and make reset/remove operate only on canonical paths.

- **High: `prepare` records invalid credential files instead of rejecting them.**
  `prepare_environment_instance` resolves and writes `credentials_env_file` through `resolve_environment_instance` at `src/planner/environments/materialize.py:88` to `src/planner/environments/materialize.py:108`, but never calls `parse_environment_file`. Credential parsing happens only at run time in `src/planner/environments/cli.py:409`. This violates the contract that `prepare` validates the registry before materialization and that malformed/duplicate/unknown/forbidden environment files are rejected (`contract.md:50`, `contract.md:84`).
  **Smallest correction:** if a credential file is supplied and exists, parse it during prepare/re-prepare with the instance kind before writing or returning the manifest. Reject malformed files before any filesystem mutation.

- **Medium: Required checked-in Linux assets are missing.**
  The contract requires checked-in account setup, live unit, parameterized non-production unit/template, env examples, and ownership/mode expectations (`contract.md:112` to `contract.md:121`, `contract.md:141`). The worktree only has env examples under `ops/panels-environments/*.env.example`; Linux behavior is generated dynamically by `src/planner/environments/linux.py:25`, and tests call the renderer directly at `tests/unit/test_environment_linux.py:24` and `tests/unit/test_environment_linux.py:78`. That does not satisfy “checked-in Linux assets” or tests that parse those units.
  **Smallest correction:** add the setup script/description and concrete systemd unit/template files under the chosen top-level directory, then make tests read/parse those checked-in files.

- **Medium: Unix socket path limits remain unvalidated.**
  The control socket is always `<instance_root>/run/server-control.sock` at `src/planner/environments/logic/registry.py:118`, but validation only checks duplicate/overlap, not AF_UNIX length (`src/planner/environments/logic/validation.py:61`, `src/planner/environments/logic/validation.py:142`). `red-green.md:514` already records `OSError: AF_UNIX path too long`, then `red-green.md:517` says the e2e uses a short `/tmp` root. The contract allows one caller-selected absolute environment root (`contract.md:58`) and requires `run` to work for prepared instances (`contract.md:85`).
  **Smallest correction:** either validate the encoded socket path length during resolve/prepare and fail clearly, or derive a short stable socket path, such as under `/tmp` with an environment-root hash, and record that in the manifest.

Review command used model `gpt-5.5`, read-only sandbox, and high reasoning effort. This is the complete reviewer output.
