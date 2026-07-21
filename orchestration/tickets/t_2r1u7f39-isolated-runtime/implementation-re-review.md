# Independent implementation re-reviews

## Re-review findings

**Findings**

- **High: Nonproduction systemd template does not reliably invoke the CLI.**
  `ops/panels-environments/panels-nonproduction@.service:11` used `${PANELS_ENVIRONMENT_INSTANCE_ARGS}` for multiple CLI arguments, and the checked-in env examples did not define the kind or those arguments.
  **Correction:** replace it with concrete staging and parameterized preview units whose complete argv is parsed in tests.

- **High: Linux credential directories are not traversable by the service users that must parse them.**
  The shared `/etc/panels/environments` parent was `root:root 0750`, so neither service account could reach its credential file.
  **Correction:** make only the shared parent searchable, retain `0640 panels-live:panels-live` for live credentials, and give staging/preview credential paths to `panels-worker`.

- **Medium: Explicit/default ports and preview ranges are not bounded to valid TCP ports.**
  Values such as `0`, negative ports, and values above `65535` could reach manifests.
  **Correction:** centralize port validation for explicit/default/manifest ports and preview ranges, including default collision checks.

- **Medium: `inspect` reports `running: false` when running state cannot be proven.**
  A lifecycle probe error was collapsed into `False`.
  **Correction:** raise a clear validation error and test forced `ServerLifecycleError`.

- **Medium: Runtime e2e proof is not green in the then-current ledger tail.**
  **Correction:** rerun from the ticket worktree, where TCP and AF_UNIX are available, and append the later green result. The final focused record ends with the passing three-process run.

## Next re-review findings

- **High: Manifest repository roots are self-authorizing for launch.**
  **Correction:** `panels environment run` now requires a caller-provided `--repository-root`, validates it against the prepared manifest, and uses exactly that root as CWD. A tampered second valid worktree is rejected before chdir or exec.

- **High: Nonproduction credentials can be placed under the future live root before live exists.**
  **Correction:** staging and preview resolution reject credential paths equal to or below `<environment_root>/live` whether or not live is prepared.

- **Medium: full `./verify` is pending.**
  **Response:** accepted as the next required gate after code review; not refuted.

- **Medium: verified commit is pending.**
  **Response:** accepted as the gate after `./verify`; not refuted.

## Orchestrator contract spot-check

The accepted contract also names repositories as per-environment mutable state. The implementation was tightened so repository roots participate in registry overlap and live-nesting checks, staging and two previews use three distinct repositories in the process-level e2e, and Linux write policy includes each environment's own repository.

## Final code-only re-review

Reviewer setup: Codex model `gpt-5.5`, read-only sandbox, high reasoning effort, after all source/test/asset corrections and before the intentionally pending `./verify` and commit gates.

```text
NO VIOLATIONS
```
