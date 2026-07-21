## Slice 1 RED

Command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_contracts.py -q
```

Decisive output:

```text
==================================== ERRORS ====================================
__________ ERROR collecting tests/unit/test_environment_contracts.py ___________
ImportError while importing test module '/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/tests/unit/test_environment_contracts.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/Library/Frameworks/Python.framework/Versions/3.13/lib/python3.13/importlib/__init__.py:88: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/unit/test_environment_contracts.py:8: in <module>
    from planner.environments.contracts import (
E   ModuleNotFoundError: No module named 'planner.environments.contracts'
=========================== short test summary info ============================
ERROR tests/unit/test_environment_contracts.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```

## Slice 1 GREEN

Command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_contracts.py -q
```

Decisive output:

```text
..............                                                           [100%]
```

## Slice 2 RED

Command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_credentials.py tests/unit/test_environment_cli.py -q
```

Decisive output:

```text
==================================== ERRORS ====================================
_________ ERROR collecting tests/unit/test_environment_credentials.py __________
ImportError while importing test module '/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/tests/unit/test_environment_credentials.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/Library/Frameworks/Python.framework/Versions/3.13/lib/python3.13/importlib/__init__.py:88: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/unit/test_environment_credentials.py:9: in <module>
    from planner.environments.logic.credentials import parse_environment_file
E   ModuleNotFoundError: No module named 'planner.environments.logic.credentials'
_____________ ERROR collecting tests/unit/test_environment_cli.py ______________
ImportError while importing test module '/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/tests/unit/test_environment_cli.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/Library/Frameworks/Python.framework/Versions/3.13/lib/python3.13/importlib/__init__.py:88: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/unit/test_environment_cli.py:9: in <module>
    from planner.environments.cli import EnvironmentCliDependencies, environment
E   ModuleNotFoundError: No module named 'planner.environments.cli'
=========================== short test summary info ============================
ERROR tests/unit/test_environment_credentials.py
ERROR tests/unit/test_environment_cli.py
!!!!!!!!!!!!!!!!!!! Interrupted: 2 errors during collection !!!!!!!!!!!!!!!!!!!!
```

## Slice 2 GREEN

Command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_credentials.py tests/unit/test_environment_cli.py -q
```

Decisive output:

```text
.......................                                                  [100%]
```

Focused Ruff command:

```sh
.venv/bin/ruff check src/planner/environments/contracts.py src/planner/environments/logic/credentials.py src/planner/environments/logic/launch_env.py src/planner/environments/cli.py tests/unit/test_environment_credentials.py tests/unit/test_environment_cli.py
```

Output:
```text
.......................                                                  [100%]
All checks passed!
```

## Second independent re-review correction RED

Command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_linux.py::test_checked_in_linux_assets_define_account_ownership_and_render_boundaries tests/unit/test_environment_linux.py::test_checked_in_staging_unit_invokes_ordinary_cli_without_env_argv_expansion tests/unit/test_environment_linux.py::test_checked_in_preview_template_uses_instance_specifier_as_preview_id tests/unit/test_environment_linux.py::test_checked_in_tmpfiles_template_keeps_live_and_worker_paths_separate tests/unit/test_environment_contracts.py::test_explicit_ports_accept_tcp_boundaries tests/unit/test_environment_contracts.py::test_explicit_ports_must_be_valid_tcp_ports tests/unit/test_environment_contracts.py::test_default_ports_accept_tcp_boundaries tests/unit/test_environment_contracts.py::test_default_live_and_staging_ports_can_be_distinct_from_preview_range tests/unit/test_environment_contracts.py::test_default_ports_reject_invalid_duplicates_and_preview_overlap tests/unit/test_environment_contracts.py::test_preview_port_range_accepts_tcp_boundaries tests/unit/test_environment_contracts.py::test_preview_port_range_must_stay_inside_tcp_boundaries tests/unit/test_environment_lifecycle.py::test_inspect_rejects_manifest_ports_outside_tcp_boundaries tests/unit/test_environment_cli.py::test_inspect_json_fails_clearly_when_running_state_cannot_be_probed -q
```

Decisive RED output:

```text
FFFF..FFF...FFFFFFF.FFF.FFFF                                             [100%]
FAILED tests/unit/test_environment_linux.py::test_checked_in_linux_assets_define_account_ownership_and_render_boundaries
E       AssertionError: assert 'traversable by the service users' in '# Panels environment Linux inputs...
FAILED tests/unit/test_environment_linux.py::test_checked_in_staging_unit_invokes_ordinary_cli_without_env_argv_expansion
E       FileNotFoundError: .../ops/panels-environments/panels-staging.service
FAILED tests/unit/test_environment_linux.py::test_checked_in_preview_template_uses_instance_specifier_as_preview_id
E       FileNotFoundError: .../ops/panels-environments/panels-preview@.service
FAILED tests/unit/test_environment_linux.py::test_checked_in_tmpfiles_template_keeps_live_and_worker_paths_separate
E       AssertionError: assert 'd /etc/panels/environments 0755 root root -' in [...]
FAILED tests/unit/test_environment_contracts.py::test_explicit_ports_must_be_valid_tcp_ports[-1]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_contracts.py::test_explicit_ports_must_be_valid_tcp_ports[0]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_contracts.py::test_explicit_ports_must_be_valid_tcp_ports[65536]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_contracts.py::test_default_ports_reject_invalid_duplicates_and_preview_overlap[<lambda>0]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_contracts.py::test_default_ports_reject_invalid_duplicates_and_preview_overlap[<lambda>1]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_contracts.py::test_default_ports_reject_invalid_duplicates_and_preview_overlap[<lambda>2]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_contracts.py::test_default_ports_reject_invalid_duplicates_and_preview_overlap[<lambda>3]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_contracts.py::test_default_ports_reject_invalid_duplicates_and_preview_overlap[<lambda>4]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_contracts.py::test_default_ports_reject_invalid_duplicates_and_preview_overlap[<lambda>5]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_contracts.py::test_default_ports_reject_invalid_duplicates_and_preview_overlap[<lambda>6]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_contracts.py::test_preview_port_range_must_stay_inside_tcp_boundaries[<lambda>0]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_contracts.py::test_preview_port_range_must_stay_inside_tcp_boundaries[<lambda>1]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_contracts.py::test_preview_port_range_must_stay_inside_tcp_boundaries[<lambda>2]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_inspect_rejects_manifest_ports_outside_tcp_boundaries[-1]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_inspect_rejects_manifest_ports_outside_tcp_boundaries[0]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_inspect_rejects_manifest_ports_outside_tcp_boundaries[65536]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_cli.py::test_inspect_json_fails_clearly_when_running_state_cannot_be_probed
E       assert 0 != 0
```

## Second independent re-review correction GREEN and current verification

Focused correction suite:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_linux.py::test_checked_in_linux_assets_define_account_ownership_and_render_boundaries tests/unit/test_environment_linux.py::test_checked_in_staging_unit_invokes_ordinary_cli_without_env_argv_expansion tests/unit/test_environment_linux.py::test_checked_in_preview_template_uses_instance_specifier_as_preview_id tests/unit/test_environment_linux.py::test_checked_in_tmpfiles_template_keeps_live_and_worker_paths_separate tests/unit/test_environment_contracts.py::test_explicit_ports_accept_tcp_boundaries tests/unit/test_environment_contracts.py::test_explicit_ports_must_be_valid_tcp_ports tests/unit/test_environment_contracts.py::test_default_ports_accept_tcp_boundaries tests/unit/test_environment_contracts.py::test_default_live_and_staging_ports_can_be_distinct_from_preview_range tests/unit/test_environment_contracts.py::test_default_ports_reject_invalid_duplicates_and_preview_overlap tests/unit/test_environment_contracts.py::test_preview_port_range_accepts_tcp_boundaries tests/unit/test_environment_contracts.py::test_preview_port_range_must_stay_inside_tcp_boundaries tests/unit/test_environment_lifecycle.py::test_inspect_rejects_manifest_ports_outside_tcp_boundaries tests/unit/test_environment_cli.py::test_inspect_json_fails_clearly_when_running_state_cannot_be_probed -q
```

Output:

```text
............................                                             [100%]
```

All environment unit tests:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_*.py -q
```

Output:

```text
........................................................................ [ 59%]
.................................................                        [100%]
```

Actual runtime e2e:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/e2e/test_environment_runtime_isolation.py -q
```

Current output:

```text
F                                                                        [100%]
FAILED tests/e2e/test_environment_runtime_isolation.py::test_environment_run_isolates_concurrent_test_mode_instances
E   AssertionError: staging exited during boot rc=1
E   PermissionError: [Errno 1] Operation not permitted
```

Direct sandbox probes:

```sh
.venv/bin/python - <<'PY'
import socket, tempfile
from pathlib import Path
p = Path(tempfile.mkdtemp(dir='/tmp')) / 'probe.sock'
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    s.bind(str(p))
    print('AF_UNIX bind ok', p)
finally:
    s.close()
    p.unlink(missing_ok=True)
PY
```

Output:

```text
PermissionError: [Errno 1] Operation not permitted
```

```sh
.venv/bin/python - <<'PY'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(('127.0.0.1', 0))
    print('TCP bind ok', s.getsockname())
finally:
    s.close()
PY
```

Output:

```text
PermissionError: [Errno 1] Operation not permitted
```

Focused Ruff:

```sh
env -u PYTHONPATH .venv/bin/ruff check src/planner/environments/contracts.py src/planner/environments/logic/registry.py src/planner/environments/logic/validation.py src/planner/environments/materialize.py src/planner/environments/cli.py tests/unit/test_environment_contracts.py tests/unit/test_environment_lifecycle.py tests/unit/test_environment_cli.py tests/unit/test_environment_linux.py
```

Output:

```text
All checks passed!
```

Diff whitespace check:

```sh
git diff --check
```

## Leaf findings RED

Command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_cli.py::test_run_requires_exactly_one_caller_trusted_repository_root tests/unit/test_environment_cli.py::test_run_rejects_manifest_repository_root_tampering_before_chdir_or_exec tests/unit/test_environment_lifecycle.py::test_prepare_rejects_nonproduction_credential_reference_under_live_before_live_exists tests/unit/test_environment_linux.py::test_checked_in_live_unit_template_uses_live_account_and_write_boundary tests/unit/test_environment_linux.py::test_checked_in_staging_unit_invokes_ordinary_cli_without_env_argv_expansion tests/unit/test_environment_linux.py::test_checked_in_preview_template_uses_instance_specifier_as_preview_id tests/unit/test_environment_linux.py::test_linux_renderer_exposes_live_account_unit_and_no_local_install tests/unit/test_environment_linux.py::test_linux_renderer_exposes_nonproduction_worker_account_and_preview_identity -q
```

Decisive RED output:

```text
F.FFFFFFF                                                                [100%]
FAILED tests/unit/test_environment_cli.py::test_run_requires_exactly_one_caller_trusted_repository_root
E       assert 0 != 0
FAILED tests/unit/test_environment_lifecycle.py::test_prepare_rejects_nonproduction_credential_reference_under_live_before_live_exists[staging-None-live/staging.env]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_prepare_rejects_nonproduction_credential_reference_under_live_before_live_exists[preview-feature-live/previews/feature.env]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_linux.py::test_checked_in_live_unit_template_uses_live_account_and_write_boundary
E       AssertionError: assert '/usr/bin/env.../environments' == '/usr/bin/env...anels/current'
FAILED tests/unit/test_environment_linux.py::test_checked_in_staging_unit_invokes_ordinary_cli_without_env_argv_expansion
E       AssertionError: assert '/usr/bin/env.../environments' == '/usr/bin/env...anels/current'
FAILED tests/unit/test_environment_linux.py::test_checked_in_preview_template_uses_instance_specifier_as_preview_id
E       AssertionError: assert '/usr/bin/env.../environments' == '/usr/bin/env...anels/current'
FAILED tests/unit/test_environment_linux.py::test_linux_renderer_exposes_live_account_unit_and_no_local_install
E       AssertionError: assert ('--repository-root ' + '/private/.../repo') in rendered.unit_text
FAILED tests/unit/test_environment_linux.py::test_linux_renderer_exposes_nonproduction_worker_account_and_preview_identity
E       AssertionError: assert ('--repository-root ' + '/private/.../repo') in rendered.unit_text
```

## Leaf findings focused GREEN

Command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_cli.py::test_run_requires_exactly_one_caller_trusted_repository_root tests/unit/test_environment_cli.py::test_run_rejects_manifest_repository_root_tampering_before_chdir_or_exec tests/unit/test_environment_lifecycle.py::test_prepare_rejects_nonproduction_credential_reference_under_live_before_live_exists tests/unit/test_environment_linux.py::test_checked_in_live_unit_template_uses_live_account_and_write_boundary tests/unit/test_environment_linux.py::test_checked_in_staging_unit_invokes_ordinary_cli_without_env_argv_expansion tests/unit/test_environment_linux.py::test_checked_in_preview_template_uses_instance_specifier_as_preview_id tests/unit/test_environment_linux.py::test_linux_renderer_exposes_live_account_unit_and_no_local_install tests/unit/test_environment_linux.py::test_linux_renderer_exposes_nonproduction_worker_account_and_preview_identity -q
```

Output:

```text
.........                                                                [100%]
```

## Final repository-root isolation verification

All environment unit tests command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_*.py -q
```

Output:

```text
........................................................................ [ 56%]
........................................................                 [100%]
```

Actual runtime e2e command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/e2e/test_environment_runtime_isolation.py -q
```

Output:

```text
F                                                                        [100%]
=================================== FAILURES ===================================
_________ test_environment_run_isolates_concurrent_test_mode_instances _________

E               AssertionError: staging exited during boot rc=1
E               Traceback (most recent call last):
E                 File "<frozen runpy>", line 198, in _run_module_as_main
E                 File "<frozen runpy>", line 88, in _run_code
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/src/planner/__main__.py", line 4, in <module>
E                   main()
E                   ~~~~^^
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/.venv/lib/python3.13/site-packages/click/core.py", line 1569, in __call__
E                   return self.main(*args, **kwargs)
E                          ~~~~~~~~~^^^^^^^^^^^^^^^^^
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/.venv/lib/python3.13/site-packages/click/core.py", line 1490, in main
E                   rv = self.invoke(ctx)
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/.venv/lib/python3.13/site-packages/click/core.py", line 1970, in invoke
E                   return _process_result(sub_ctx.command.invoke(sub_ctx))
E                                          ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/.venv/lib/python3.13/site-packages/click/core.py", line 1353, in invoke
E                   return ctx.invoke(self.callback, **ctx.params)
E                          ~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/.venv/lib/python3.13/site-packages/click/core.py", line 907, in invoke
E                   return callback(*args, **kwargs)
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/src/planner/cli/main.py", line 294, in serve
E                   result = run_server_supervisor()
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/src/planner/server_lifecycle/supervisor.py", line 349, in run_server_supervisor
E                   return supervisor.run()
E                          ~~~~~~~~~~~~~~^^
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/src/planner/server_lifecycle/supervisor.py", line 99, in run
E                   self._bind_control_socket()
E                   ~~~~~~~~~~~~~~~~~~~~~~~~~^^
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/src/planner/server_lifecycle/supervisor.py", line 170, in _bind_control_socket
E                   listener.bind(str(self._control_socket_path))
E                   ~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E               PermissionError: [Errno 1] Operation not permitted

FAILED tests/e2e/test_environment_runtime_isolation.py::test_environment_run_isolates_concurrent_test_mode_instances
```

AF_UNIX probe command:

```sh
env -u PYTHONPATH .venv/bin/python - <<'PY'
import socket, tempfile, pathlib
root=pathlib.Path(tempfile.mkdtemp(dir='/private/tmp'))
path=root/'sock'
s=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    s.bind(str(path))
    print('bind-ok', path)
finally:
    s.close()
PY
```

Output:

```text
Traceback (most recent call last):
  File "<stdin>", line 6, in <module>
PermissionError: [Errno 1] Operation not permitted
```

Focused Ruff command:

```sh
env -u PYTHONPATH .venv/bin/ruff check src/planner/environments/logic/validation.py src/planner/environments/logic/registry.py src/planner/environments/materialize.py src/planner/environments/cli.py src/planner/environments/linux.py tests/unit/test_environment_contracts.py tests/unit/test_environment_lifecycle.py tests/unit/test_environment_cli.py tests/unit/test_environment_linux.py tests/unit/test_environment_fake_fixture.py tests/e2e/test_environment_runtime_isolation.py
```

Output:

```text
All checks passed!
```

Diff whitespace check command:

```sh
git diff --check
```

Output:

```text
```

## Final post-cleanup verification

All environment unit tests command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_*.py -q
```

Output:

```text
........................................................................ [ 56%]
........................................................                 [100%]
```

Focused Ruff command:

```sh
env -u PYTHONPATH .venv/bin/ruff check src/planner/environments/logic/validation.py src/planner/environments/logic/registry.py src/planner/environments/materialize.py src/planner/environments/cli.py src/planner/environments/linux.py tests/unit/test_environment_contracts.py tests/unit/test_environment_lifecycle.py tests/unit/test_environment_cli.py tests/unit/test_environment_linux.py tests/unit/test_environment_fake_fixture.py tests/e2e/test_environment_runtime_isolation.py
```

Output:

```text
All checks passed!
```

Diff whitespace check command:

```sh
git diff --check
```

Output:

```text
```
```

Output:

```text
```

## Final repository-root isolation RED

Command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_contracts.py::test_registry_rejects_cross_instance_repository_root_overlap tests/unit/test_environment_contracts.py::test_registry_rejects_nested_repository_roots_across_instances tests/unit/test_environment_lifecycle.py::test_prepare_rejects_repository_root_shared_with_prepared_instance tests/unit/test_environment_linux.py::test_checked_in_linux_assets_define_account_ownership_and_render_boundaries tests/unit/test_environment_linux.py::test_checked_in_live_unit_template_uses_live_account_and_write_boundary tests/unit/test_environment_linux.py::test_checked_in_staging_unit_invokes_ordinary_cli_without_env_argv_expansion tests/unit/test_environment_linux.py::test_checked_in_preview_template_uses_instance_specifier_as_preview_id tests/unit/test_environment_linux.py::test_linux_renderer_exposes_live_account_unit_and_no_local_install tests/unit/test_environment_linux.py::test_linux_renderer_exposes_nonproduction_worker_account_and_preview_identity -q
```

Output:

```text
FFFFFFFFF                                                                [100%]
=================================== FAILURES ===================================
_________ test_registry_rejects_cross_instance_repository_root_overlap _________

E       Failed: DID NOT RAISE EnvironmentValidationError

________ test_registry_rejects_nested_repository_roots_across_instances ________

E           FileExistsError: [Errno 17] File exists: '/private/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/pytest-of-khushaljagota/pytest-3227/test_registry_rejects_nested_r0/live-repo'

______ test_prepare_rejects_repository_root_shared_with_prepared_instance ______

E       Failed: DID NOT RAISE EnvironmentValidationError

_ test_checked_in_linux_assets_define_account_ownership_and_render_boundaries __

E       AssertionError: assert '/opt/panels/live' in '# Panels environment Linux inputs\n\nThese files are checked-in render/install inputs only. They do not install users... writes belong\nunder `/var/lib/panels/environments`, and credential files belong under\n`/etc/panels/environments`.\n'

___ test_checked_in_live_unit_template_uses_live_account_and_write_boundary ____

E       AssertionError: assert '/opt/panels/current' == '/opt/panels/live'
E         - /opt/panels/live
E         + /opt/panels/current

_ test_checked_in_staging_unit_invokes_ordinary_cli_without_env_argv_expansion _

E       AssertionError: assert '/opt/panels/current' == '/opt/panels/staging'
E         - /opt/panels/staging
E         + /opt/panels/current

____ test_checked_in_preview_template_uses_instance_specifier_as_preview_id ____

E       AssertionError: assert '/opt/panels/current' == '/opt/panels/previews/%i'
E         - /opt/panels/previews/%i
E         + /opt/panels/current

______ test_linux_renderer_exposes_live_account_unit_and_no_local_install ______

E       AssertionError: assert ((('ReadWritePaths=' + '/private/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/pytest-of-khushaljagota/pytest-3227/test_linux_renderer_exposes_li0/envs/live') + ' ') + '/private/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/pytest-of-khushaljagota/pytest-3227/test_linux_renderer_exposes_li0/repo') in '[Unit]\nDescription=Panels environment live (live)\nAfter=network-online.target\nWants=network-online.target\n\n[Serv...ders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/pytest-of-khushaljagota/pytest-3227/test_linux_renderer_exposes_li0/envs/live'

_ test_linux_renderer_exposes_nonproduction_worker_account_and_preview_identity _

E       AssertionError: assert ((('ReadWritePaths=' + '/private/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/pytest-of-khushaljagota/pytest-3227/test_linux_renderer_exposes_no0/envs/previews/feature-123') + ' ') + '/private/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/pytest-of-khushaljagota/pytest-3227/test_linux_renderer_exposes_no0/repo') in '[Unit]\nDescription=Panels environment preview (feature-123)\nAfter=network-online.target\nWants=network-online.targe...33nc05srgprf9j5c0000gn/T/pytest-of-khushaljagota/pytest-3227/test_linux_renderer_exposes_no0/envs/previews/feature-123'

=========================== short test summary info ============================
FAILED tests/unit/test_environment_contracts.py::test_registry_rejects_cross_instance_repository_root_overlap
FAILED tests/unit/test_environment_contracts.py::test_registry_rejects_nested_repository_roots_across_instances
FAILED tests/unit/test_environment_lifecycle.py::test_prepare_rejects_repository_root_shared_with_prepared_instance
FAILED tests/unit/test_environment_linux.py::test_checked_in_linux_assets_define_account_ownership_and_render_boundaries
FAILED tests/unit/test_environment_linux.py::test_checked_in_live_unit_template_uses_live_account_and_write_boundary
FAILED tests/unit/test_environment_linux.py::test_checked_in_staging_unit_invokes_ordinary_cli_without_env_argv_expansion
FAILED tests/unit/test_environment_linux.py::test_checked_in_preview_template_uses_instance_specifier_as_preview_id
FAILED tests/unit/test_environment_linux.py::test_linux_renderer_exposes_live_account_unit_and_no_local_install
FAILED tests/unit/test_environment_linux.py::test_linux_renderer_exposes_nonproduction_worker_account_and_preview_identity
```

## Final repository-root isolation GREEN

Command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_contracts.py::test_registry_rejects_cross_instance_repository_root_overlap tests/unit/test_environment_contracts.py::test_registry_rejects_nested_repository_roots_across_instances tests/unit/test_environment_lifecycle.py::test_prepare_rejects_repository_root_shared_with_prepared_instance tests/unit/test_environment_linux.py::test_checked_in_linux_assets_define_account_ownership_and_render_boundaries tests/unit/test_environment_linux.py::test_checked_in_live_unit_template_uses_live_account_and_write_boundary tests/unit/test_environment_linux.py::test_checked_in_staging_unit_invokes_ordinary_cli_without_env_argv_expansion tests/unit/test_environment_linux.py::test_checked_in_preview_template_uses_instance_specifier_as_preview_id tests/unit/test_environment_linux.py::test_linux_renderer_exposes_live_account_unit_and_no_local_install tests/unit/test_environment_linux.py::test_linux_renderer_exposes_nonproduction_worker_account_and_preview_identity -q
```

Output:

```text
.........                                                                [100%]
```

## Slice 2 scope correction: Tailscale setup is not a runtime credential

The initial Slice 2 default policy admitted `TAILSCALE_AUTHKEY` for live. That crossed the accepted boundary: final VPS/Tailscale setup is a separate Ticket, and the Panels service does not earn that host-provisioning credential.

RED command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_credentials.py::test_default_environment_policy_keeps_tailscale_setup_outside_runtime -q
```

Decisive RED:

```text
FAILED tests/unit/test_environment_credentials.py::test_default_environment_policy_keeps_tailscale_setup_outside_runtime
E       Failed: DID NOT RAISE EnvironmentValidationError
```

GREEN commands and decisive output:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_credentials.py::test_default_environment_policy_keeps_tailscale_setup_outside_runtime -q
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_contracts.py tests/unit/test_environment_credentials.py tests/unit/test_environment_cli.py -q
env -u PYTHONPATH .venv/bin/ruff check src/planner/environments tests/unit/test_environment_contracts.py tests/unit/test_environment_credentials.py tests/unit/test_environment_cli.py
```

```text
.                                                                        [100%]
......................................                                   [100%]
All checks passed!
```

Production-only policy behavior remains covered through an injected abstract policy; the checked-in default now contains only model-provider credential names.

Slice 1 preservation check:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_contracts.py -q
```

Output:

```text
..............                                                           [100%]
```

## Slice 3 RED

Command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_fake_fixture.py tests/unit/test_environment_cli.py -q
```

Decisive output:

```text
==================================== ERRORS ====================================
_________ ERROR collecting tests/unit/test_environment_fake_fixture.py _________
ImportError while importing test module '/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/tests/unit/test_environment_fake_fixture.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/Library/Frameworks/Python.framework/Versions/3.13/lib/python3.13/importlib/__init__.py:88: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/unit/test_environment_fake_fixture.py:7: in <module>
    from planner.environments.fake_fixture import (
E   ModuleNotFoundError: No module named 'planner.environments.fake_fixture'
=========================== short test summary info ============================
ERROR tests/unit/test_environment_fake_fixture.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```

## Slice 3 GREEN

Command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_fake_fixture.py tests/unit/test_environment_cli.py -q
```

Decisive output:

```text
..............                                                           [100%]
```

All environment unit tests so far:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_contracts.py tests/unit/test_environment_credentials.py tests/unit/test_environment_fake_fixture.py tests/unit/test_environment_cli.py -q
```

Output:

```text
................................................                         [100%]
```

Focused Ruff:

```sh
env -u PYTHONPATH .venv/bin/ruff check src/planner/environments/fake_fixture.py src/planner/environments/materialize.py src/planner/environments/cli.py tests/unit/test_environment_fake_fixture.py tests/unit/test_environment_cli.py
```

Output:

```text
All checks passed!
```

## Final Slice 6 RED

Command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_linux.py tests/unit/test_environment_hermes_smoke.py tests/unit/test_environment_cli.py -q
```

Decisive output:

```text
==================================== ERRORS ====================================
____________ ERROR collecting tests/unit/test_environment_linux.py _____________
ImportError while importing test module '/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/tests/unit/test_environment_linux.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/Library/Frameworks/Python.framework/Versions/3.13/lib/python3.13/importlib/__init__.py:88: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/unit/test_environment_linux.py:6: in <module>
    from planner.environments.linux import render_linux_specification
E   ModuleNotFoundError: No module named 'planner.environments.linux'
_________ ERROR collecting tests/unit/test_environment_hermes_smoke.py _________
ImportError while importing test module '/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/tests/unit/test_environment_hermes_smoke.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/Library/Frameworks/Python.framework/Versions/3.13/lib/python3.13/importlib/__init__.py:88: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/unit/test_environment_hermes_smoke.py:8: in <module>
    from planner.environments.hermes_smoke import (
E   ModuleNotFoundError: No module named 'planner.environments.hermes_smoke'
=========================== short test summary info ============================
ERROR tests/unit/test_environment_linux.py
ERROR tests/unit/test_environment_hermes_smoke.py
!!!!!!!!!!!!!!!!!!! Interrupted: 2 errors during collection !!!!!!!!!!!!!!!!!!!!
```

## Final Slice 6 GREEN

Command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_linux.py tests/unit/test_environment_hermes_smoke.py tests/unit/test_environment_cli.py -q
```

Output:

```text
...................                                                      [100%]
```

All environment unit tests:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_*.py -q
```

Output:

```text
....................................................................     [100%]
```

Runtime-isolation e2e:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/e2e/test_environment_runtime_isolation.py -q
```

Output:

```text
F                                                                        [100%]
=================================== FAILURES ===================================
_________ test_environment_run_isolates_concurrent_test_mode_instances _________

tmp_path = PosixPath('/private/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/pytest-of-khushaljagota/pytest-3190/test_environment_run_isolates_0')

    def test_environment_run_isolates_concurrent_test_mode_instances(tmp_path: Path) -> None:
        environment_root = Path("/tmp") / f"panels-env-runtime-{os.getpid()}-{tmp_path.name[-8:]}"
        repository_root = REPO_ROOT
        instances: list[RuntimeInstance] = []
        processes: list[RuntimeProcess] = []

        try:
            instances = [
                _prepare_instance(
                    tmp_path,
                    kind="staging",
                    instance_id=None,
                    environment_root=environment_root,
                    repository_root=repository_root,
                    port=_test_port(tmp_path, 0),
                ),
                _prepare_instance(
                    tmp_path,
                    kind="preview",
                    instance_id="alpha",
                    environment_root=environment_root,
                    repository_root=repository_root,
                    port=_test_port(tmp_path, 1),
                ),
                _prepare_instance(
                    tmp_path,
                    kind="preview",
                    instance_id="bravo",
                    environment_root=environment_root,
                    repository_root=repository_root,
                    port=_test_port(tmp_path, 2),
                ),
            ]
            processes = [
                _start_instance(instance, repository_root, environment_root)
                for instance in instances
            ]
            for runtime in processes:
>               _wait_for_ready(runtime)

tests/e2e/test_environment_runtime_isolation.py:74:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

runtime = RuntimeProcess(instance=RuntimeInstance(kind='staging', instance_id=None, manifest={'credentials_env_file': None, 'db_...solates_0/staging-stable.log')), proc=<Popen: returncode: 1 args: ['/Users/khushaljagota/.hermes/worktrees/plannin...>)

    def _wait_for_ready(runtime: RuntimeProcess) -> None:
        deadline = time.monotonic() + BOOT_BUDGET_S
        while time.monotonic() < deadline:
            if runtime.proc.poll() is not None:
>               raise AssertionError(
                    f"{runtime.instance.kind} exited during boot rc={runtime.proc.returncode}\n"
                    f"{_log_tail(runtime.instance.log_path)}"
                )
E               AssertionError: staging exited during boot rc=1
E               Traceback (most recent call last):
E                 File "<frozen runpy>", line 198, in _run_module_as_main
E                 File "<frozen runpy>", line 88, in _run_code
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/src/planner/__main__.py", line 4, in <module>
E                   main()
E                   ~~~~^^
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/.venv/lib/python3.13/site-packages/click/core.py", line 1569, in __call__
E                   return self.main(*args, **kwargs)
E                          ~~~~~~~~~^^^^^^^^^^^^^^^^^
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/.venv/lib/python3.13/site-packages/click/core.py", line 1490, in main
E                   rv = self.invoke(ctx)
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/.venv/lib/python3.13/site-packages/click/core.py", line 1970, in invoke
E                   return _process_result(sub_ctx.command.invoke(sub_ctx))
E                                          ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/.venv/lib/python3.13/site-packages/click/core.py", line 1353, in invoke
E                   return ctx.invoke(self.callback, **ctx.params)
E                          ~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/.venv/lib/python3.13/site-packages/click/core.py", line 907, in invoke
E                   return callback(*args, **kwargs)
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/src/planner/cli/main.py", line 294, in serve
E                   result = run_server_supervisor()
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/src/planner/server_lifecycle/supervisor.py", line 349, in run_server_supervisor
E                   return supervisor.run()
E                          ~~~~~~~~~~~~~~^^
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/src/planner/server_lifecycle/supervisor.py", line 99, in run
E                   self._bind_control_socket()
E                   ~~~~~~~~~~~~~~~~~~~~~~~~~^^
E                 File "/Users/khushaljagota/.hermes/worktrees/planning-v2-t_2r1u7f39/src/planner/server_lifecycle/supervisor.py", line 170, in _bind_control_socket
E                   listener.bind(str(self._control_socket_path))
E                   ~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E               PermissionError: [Errno 1] Operation not permitted

tests/e2e/test_environment_runtime_isolation.py:247: AssertionError
=========================== short test summary info ============================
FAILED tests/e2e/test_environment_runtime_isolation.py::test_environment_run_isolates_concurrent_test_mode_instances
```

Focused Ruff:

```sh
env -u PYTHONPATH .venv/bin/ruff check src/planner/environments src/planner/cli/main.py tests/unit/test_environment_*.py tests/e2e/test_environment_runtime_isolation.py
```

Output:

```text
All checks passed!
```

Docs link check:

```sh
env -u PYTHONPATH .venv/bin/python - <<'PY'
from __future__ import annotations

import re
from pathlib import Path

root = Path('docs')
missing: list[str] = []
for path in sorted(root.glob('*.md')):
    text = path.read_text(encoding='utf-8')
    for match in re.finditer(r'\[[^\]]+\]\(([^)]+)\)', text):
        target = match.group(1).split('#', 1)[0]
        if not target or '://' in target or target.startswith('mailto:'):
            continue
        candidate = (path.parent / target).resolve()
        if not candidate.exists():
            missing.append(f'{path}:{match.start(1)} -> {target}')
if missing:
    print('BROKEN DOC LINKS')
    print('\n'.join(missing))
    raise SystemExit(1)
print('docs link check passed')
PY
```

Output:

```text
docs link check passed
```

## Slice 4 RED

Command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_lifecycle.py tests/unit/test_environment_cli.py -q
```

Decisive output:

```text
FFFFFFFF........FFFF                                                     [100%]
FAILED tests/unit/test_environment_lifecycle.py::test_prepare_is_exactly_idempotent_for_existing_manifest
E       AssertionError: assert 'bb6624c47cc0...b6a2080bdbe64' == '32822e61f2c6...186fe463ea693'
FAILED tests/unit/test_environment_lifecycle.py::test_mismatched_reprepare_fails_without_mutating_existing_instance
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_concurrent_preview_prepares_allocate_distinct_durable_ports
E       TypeError: prepare_environment_instance() got an unexpected keyword argument 'defaults'
FAILED tests/unit/test_environment_lifecycle.py::test_prepare_validates_existing_registry_manifests_before_writing
E       TypeError: prepare_environment_instance() got an unexpected keyword argument 'defaults'
FAILED tests/unit/test_environment_lifecycle.py::test_reset_and_remove_fail_while_server_lifecycle_lease_is_held
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_reset_holds_lifecycle_lease_until_failed_fixture_cleanup_finishes
E       Failed: DID NOT RAISE ServerLifecycleAlreadyOwnedError
FAILED tests/unit/test_environment_lifecycle.py::test_remove_refuses_live_and_removes_only_selected_instance
E       TypeError: prepare_environment_instance() got an unexpected keyword argument 'defaults'
FAILED tests/unit/test_environment_lifecycle.py::test_remove_holds_lifecycle_lease_until_failed_cleanup_finishes
E       AttributeError: module 'planner.environments.materialize' has no attribute 'remove_environment_instance'
FAILED tests/unit/test_environment_cli.py::test_preview_inspect_loads_prepared_manifest_without_repeated_options
E       AssertionError: Error: preview port must be allocated by the caller
FAILED tests/unit/test_environment_cli.py::test_preview_run_loads_prepared_manifest_without_repeated_options
E       AssertionError: Error: preview port must be allocated by the caller
FAILED tests/unit/test_environment_cli.py::test_preview_reset_cli_loads_actual_prepared_port_for_lifecycle_safety
E       AssertionError: Error: preview port must be allocated by the caller
FAILED tests/unit/test_environment_cli.py::test_remove_cli_removes_prepared_preview_without_repeated_options
E       AssertionError: Error: No such command 'remove'.
```

## Slice 4 GREEN

Command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_lifecycle.py tests/unit/test_environment_cli.py -q
```

Output:

```text
....................                                                     [100%]
```

All environment unit tests:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_*.py -q
```

Output:

```text
............................................................             [100%]
```

Focused Ruff:

```sh
env -u PYTHONPATH .venv/bin/ruff check src/planner/environments/materialize.py src/planner/environments/logic/registry.py src/planner/environments/cli.py tests/unit/test_environment_lifecycle.py tests/unit/test_environment_cli.py
```

Output:

```text
All checks passed!
```

## Slice 5 runtime integration RED

The ordinary CLI was exercised from a subprocess before registration and then with three concurrent servers. The decisive REDs were:

```text
Error: No such command 'environment'.
OSError: AF_UNIX path too long
```

The first proved the top-level CLI registration was missing. The second proved a long temporary root could not host the configured Unix socket; the runtime e2e now uses a short isolated `/tmp` root while the manifest continues to expose the socket contract.

## Final integration GREEN

Commands:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_*.py -q
env -u PYTHONPATH .venv/bin/pytest tests/e2e/test_environment_runtime_isolation.py -q
env -u PYTHONPATH .venv/bin/ruff check src/planner/environments src/planner/cli/main.py tests/unit/test_environment_*.py tests/e2e/test_environment_runtime_isolation.py
git diff --check
```

Output:

```text
....................................................................     [100%]
.                                                                        [100%]
All checks passed!
```

The process-level e2e started staging and two previews concurrently through `panels environment run --test-mode`, waited for HTTP readiness, mutated each database and managed-file tree independently, observed distinct ports/control sockets/locks/Hermes homes, created isolated fake Hermes session state, terminated all three process groups, and removed only the temporary instances.

## Independent review correction RED

Command:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_contracts.py::test_control_socket_path_length_is_rejected_during_resolution tests/unit/test_environment_lifecycle.py::test_inspect_rejects_tampered_manifest_fields tests/unit/test_environment_lifecycle.py::test_remove_rejects_tampered_instance_root_and_never_deletes_that_path tests/unit/test_environment_lifecycle.py::test_reset_rejects_tampered_environment_root_before_materializing_there tests/unit/test_environment_lifecycle.py::test_prepare_rejects_existing_invalid_credential_file_before_lock_or_state tests/unit/test_environment_lifecycle.py::test_exact_reprepare_revalidates_existing_credential_file_before_returning tests/unit/test_environment_cli.py::test_run_rejects_tampered_manifest_before_exec tests/unit/test_environment_linux.py::test_checked_in_linux_assets_define_account_ownership_and_render_boundaries tests/unit/test_environment_linux.py::test_checked_in_live_unit_template_uses_live_account_and_write_boundary tests/unit/test_environment_linux.py::test_checked_in_nonproduction_unit_template_is_parameterized_worker_runtime tests/unit/test_environment_linux.py::test_checked_in_tmpfiles_template_keeps_live_and_worker_paths_separate tests/unit/test_environment_linux.py::test_checked_in_environment_examples_are_names_only -q
```

Decisive RED output:

```text
FFFFFFFFFFFFFFFFFFFFFFFFFF.                                              [100%]
FAILED tests/unit/test_environment_contracts.py::test_control_socket_path_length_is_rejected_during_resolution
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_inspect_rejects_tampered_manifest_fields[kind-staging]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_inspect_rejects_tampered_manifest_fields[instance_id-other-feature]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_inspect_rejects_tampered_manifest_fields[environment_root-outside-root]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_inspect_rejects_tampered_manifest_fields[instance_root-outside-root/previews/feature]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_inspect_rejects_tampered_manifest_fields[db_path-outside-root/data/planner.db]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_inspect_rejects_tampered_manifest_fields[managed_files_root-outside-root/data/files]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_inspect_rejects_tampered_manifest_fields[hermes_home-outside-root/hermes-home]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_inspect_rejects_tampered_manifest_fields[logs_dir-outside-root/logs]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_inspect_rejects_tampered_manifest_fields[dispatcher_lock_path-outside-root/run/dispatcher.lock]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_inspect_rejects_tampered_manifest_fields[server_control_socket_path-outside-root/run/server-control.sock]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_inspect_rejects_tampered_manifest_fields[expected_linux_account-panels-live]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_inspect_rejects_tampered_manifest_fields[fixture_version-tampered-fixture]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_inspect_rejects_tampered_manifest_fields[repository_roots-outside-root/repository]
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_remove_rejects_tampered_instance_root_and_never_deletes_that_path
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_reset_rejects_tampered_environment_root_before_materializing_there
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_lifecycle.py::test_prepare_rejects_existing_invalid_credential_file_before_lock_or_state[ANTHROPIC_API_KEY-malformed]
E       AssertionError: credential validation must happen before registry lock
FAILED tests/unit/test_environment_lifecycle.py::test_prepare_rejects_existing_invalid_credential_file_before_lock_or_state[ANTHROPIC_API_KEY=one\nANTHROPIC_API_KEY=two-duplicate]
E       AssertionError: credential validation must happen before registry lock
FAILED tests/unit/test_environment_lifecycle.py::test_prepare_rejects_existing_invalid_credential_file_before_lock_or_state[STRIPE_SECRET_KEY=secret-unknown]
E       AssertionError: credential validation must happen before registry lock
FAILED tests/unit/test_environment_lifecycle.py::test_prepare_rejects_existing_invalid_credential_file_before_lock_or_state[PLAN_DB_PATH=/tmp/poison.db-forbidden]
E       AssertionError: credential validation must happen before registry lock
FAILED tests/unit/test_environment_lifecycle.py::test_exact_reprepare_revalidates_existing_credential_file_before_returning
E       Failed: DID NOT RAISE EnvironmentValidationError
FAILED tests/unit/test_environment_cli.py::test_run_rejects_tampered_manifest_before_exec
E       assert 0 != 0
FAILED tests/unit/test_environment_linux.py::test_checked_in_linux_assets_define_account_ownership_and_render_boundaries
E       FileNotFoundError: .../ops/panels-environments/README.md
FAILED tests/unit/test_environment_linux.py::test_checked_in_live_unit_template_uses_live_account_and_write_boundary
E       FileNotFoundError: .../ops/panels-environments/panels-live.service
FAILED tests/unit/test_environment_linux.py::test_checked_in_nonproduction_unit_template_is_parameterized_worker_runtime
E       FileNotFoundError: .../ops/panels-environments/panels-nonproduction@.service
FAILED tests/unit/test_environment_linux.py::test_checked_in_tmpfiles_template_keeps_live_and_worker_paths_separate
E       FileNotFoundError: .../ops/panels-environments/panels-environments.tmpfiles
```

## Independent review correction GREEN and verification

Exact new tests:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_contracts.py::test_control_socket_path_length_is_rejected_during_resolution tests/unit/test_environment_lifecycle.py::test_inspect_rejects_tampered_manifest_fields tests/unit/test_environment_lifecycle.py::test_remove_rejects_tampered_instance_root_and_never_deletes_that_path tests/unit/test_environment_lifecycle.py::test_reset_rejects_tampered_environment_root_before_materializing_there tests/unit/test_environment_lifecycle.py::test_prepare_rejects_existing_invalid_credential_file_before_lock_or_state tests/unit/test_environment_lifecycle.py::test_exact_reprepare_revalidates_existing_credential_file_before_returning tests/unit/test_environment_cli.py::test_run_rejects_tampered_manifest_before_exec tests/unit/test_environment_linux.py::test_checked_in_linux_assets_define_account_ownership_and_render_boundaries tests/unit/test_environment_linux.py::test_checked_in_live_unit_template_uses_live_account_and_write_boundary tests/unit/test_environment_linux.py::test_checked_in_nonproduction_unit_template_is_parameterized_worker_runtime tests/unit/test_environment_linux.py::test_checked_in_tmpfiles_template_keeps_live_and_worker_paths_separate tests/unit/test_environment_linux.py::test_checked_in_environment_examples_are_names_only -q
```

Output:

```text
...........................                                              [100%]
```

All environment unit tests:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_*.py -q
```

Output:

```text
........................................................................ [ 75%]
........................                                                 [100%]
```

Runtime e2e:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/e2e/test_environment_runtime_isolation.py -q
```

Output:

```text
F                                                                        [100%]
FAILED tests/e2e/test_environment_runtime_isolation.py::test_environment_run_isolates_concurrent_test_mode_instances
E   AssertionError: staging exited during boot rc=1
E   PermissionError: [Errno 1] Operation not permitted
```

Direct socket probes in this runner also fail with `PermissionError: [Errno 1] Operation not permitted` for AF_UNIX bind under `/tmp` and `/private/tmp`, and unrelated server-lifecycle socket tests fail before application code on TCP/AF_UNIX bind for the same reason. The e2e was run, but this sandbox does not permit the socket operations it asserts.

Focused Ruff:

```sh
env -u PYTHONPATH .venv/bin/ruff check src/planner/environments/materialize.py src/planner/environments/logic/validation.py src/planner/environments/logic/registry.py src/planner/environments/linux.py tests/unit/test_environment_contracts.py tests/unit/test_environment_lifecycle.py tests/unit/test_environment_cli.py tests/unit/test_environment_linux.py tests/unit/test_environment_credentials.py tests/unit/test_environment_fake_fixture.py tests/e2e/test_environment_runtime_isolation.py
```

Output:

```text
All checks passed!
```

## Second independent review correction RED

The focused correction suite failed on the intended seams before implementation:

```text
FAILED tests/unit/test_environment_linux.py::test_checked_in_nonproduction_units_have_concrete_argv
FAILED tests/unit/test_environment_linux.py::test_checked_in_linux_credential_paths_are_traversable_without_exposing_live_credentials
FAILED tests/unit/test_environment_contracts.py::test_explicit_ports_must_stay_inside_tcp_boundaries
FAILED tests/unit/test_environment_contracts.py::test_preview_port_range_must_stay_inside_tcp_boundaries
FAILED tests/unit/test_environment_lifecycle.py::test_inspect_rejects_manifest_ports_outside_tcp_boundaries
FAILED tests/unit/test_environment_cli.py::test_inspect_json_fails_clearly_when_running_state_cannot_be_probed
```

## Second independent review correction GREEN

Commands:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_*.py -q
env -u PYTHONPATH .venv/bin/pytest tests/e2e/test_environment_runtime_isolation.py -q
env -u PYTHONPATH .venv/bin/ruff check src/planner/environments src/planner/cli/main.py tests/unit/test_environment_*.py tests/e2e/test_environment_runtime_isolation.py
git diff --check
```

Current output from the ticket worktree:

```text
........................................................................ [ 59%]
.................................................                        [100%]
.                                                                        [100%]
All checks passed!
```

This later run supersedes the read-only reviewer sandbox's recorded socket-permission failure above. The ticket worktree runner permits the required TCP and AF_UNIX operations, and the three-process runtime test is green.

## Leaf findings broad verification

All environment unit tests:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_*.py -q
```

Output:

```text
........................................................................ [ 57%]
.....................................................                    [100%]
```

Runtime e2e:

```sh
env -u PYTHONPATH .venv/bin/pytest tests/e2e/test_environment_runtime_isolation.py -q
```

Output:

```text
F                                                                        [100%]
FAILED tests/e2e/test_environment_runtime_isolation.py::test_environment_run_isolates_concurrent_test_mode_instances
E   AssertionError: staging exited during boot rc=1
E   PermissionError: [Errno 1] Operation not permitted
```

The subprocess reached `server_lifecycle.supervisor._bind_control_socket`, then this
runner rejected AF_UNIX `bind`. No application assertion after server boot ran.

Focused Ruff:

```sh
env -u PYTHONPATH .venv/bin/ruff check src/planner/environments/logic/validation.py src/planner/environments/logic/registry.py src/planner/environments/materialize.py src/planner/environments/cli.py src/planner/environments/linux.py tests/unit/test_environment_cli.py tests/unit/test_environment_lifecycle.py tests/unit/test_environment_linux.py tests/e2e/test_environment_runtime_isolation.py
```

Output:

```text
All checks passed!
```

Diff whitespace check:

```sh
git diff --check
```

Output:

```text
```

## Final repository-isolation GREEN

The ticket worktree was re-run outside the read-only agent sandbox after repository roots became per-environment mutable state.

```sh
env -u PYTHONPATH .venv/bin/pytest tests/unit/test_environment_*.py -q
env -u PYTHONPATH .venv/bin/pytest tests/e2e/test_environment_runtime_isolation.py -q
env -u PYTHONPATH .venv/bin/ruff check src/planner/environments src/planner/cli/main.py tests/unit/test_environment_*.py tests/e2e/test_environment_runtime_isolation.py
git diff --check
```

Output:

```text
........................................................................ [ 56%]
........................................................                 [100%]
.                                                                        [100%]
All checks passed!
```

The later green runtime result supersedes the immediately preceding sandbox-only AF_UNIX denial. Staging and both previews now launch from three distinct caller-trusted repository roots as well as distinct databases, files, Hermes homes, logs, locks, sockets, and ports.

## Canonical repository verification

The first full run found three missing return annotations in the new fake-fixture helpers and an ambient worker `PLAN_TICKET_ID` contaminating one unrelated CLI test. The annotations were added, and the verification environment was rebuilt from an explicit allowlist. A later full run had two unrelated timing failures; both exact tests passed immediately in isolation without source changes.

The final clean run used the unchanged settled tree:

```sh
env -i HOME="$HOME" PATH="$PATH" TMPDIR="${TMPDIR:-/tmp}" LANG="${LANG:-en_US.UTF-8}" ./verify
```

Full transcript: `data/verify/t_2r1u7f39-isolated-runtime-clean.log` (149 lines).
SHA-256: `252674abdfecfa4407c988904641748296674ee41614128c203ce1be55eb8972`.

Decisive output:

```text
[verify] gate ruff: ok
[verify] gate mypy: ok
[verify] gate unit suite: ok
[verify] gate build check: ok
[verify] gate frontend: ok
[verify] gate e2e suite: ok

1167 passed, 9 warnings
136 passed
VERIFY: PASS
```

## Closeout RED: official ACP smoke credential inheritance

Command:

```sh
PYTHONPATH=src /Users/khushaljagota/.hermes/planning-v2/.venv/bin/pytest tests/unit/test_environment_hermes_smoke.py::test_official_acp_smoke_child_keeps_only_validated_credentials_and_isolated_home -q
```

Decisive output:

```text
TypeError: _create_official_acp_session() got an unexpected keyword argument 'credential_environment_names'
```

## Closeout GREEN: official ACP smoke credential inheritance

The smoke subprocess now passes credential names, never credential values, to the official ACP
child. The child revalidates the names and extends only this opt-in definition; production Hermes
callers retain the standard inheritance tuple.

```sh
PYTHONPATH=src /Users/khushaljagota/.hermes/planning-v2/.venv/bin/pytest tests/unit/test_environment_hermes_smoke.py::test_official_acp_smoke_child_keeps_only_validated_credentials_and_isolated_home -q
```

Output:

```text
.                                                                        [100%]
```
