# Plan review — t_ub13wga8 lightweight VPS status and safe cleanup

Reviewed `AGENTS.md`, `CLAUDE.md`, `PRINCIPLES.md`, the ticket contract and
dispatch, and the dispatched source/test seams as they stand on this worktree.

## Findings

1. **HIGH — cleanup inventory lifecycle is contradictory and cannot literally meet the stated safety invariant.**

   Step 4 says that every invocation builds one in-memory `CleanupInventory`,
   while also saying that a dry-run serialises an inventory and `--apply`
   consumes *that exact inventory*, with no stored preview and no rerun-as-apply
   shortcut. The normal `panels vps-cleanup` then `panels vps-cleanup --apply`
   sequence necessarily crosses two HTTP requests/process invocations, so the
   apply cannot consume the prior dry-run inventory. This leaves the
   dry-run/apply contract and its proof tests undefined.

   **Correction:** define one coherent lifecycle before implementation. The
   smallest compatible choice is: each request constructs exactly one fresh
   inventory; dry-run returns it without mutation; `--apply` constructs its own
   inventory once, immediately re-proves every selected target, and applies that
   same in-request inventory. Replace “that exact inventory” and
   “no rerun-as-apply shortcut” with this meaning, and add focused tests proving
   that a separately run dry-run never authorizes a later apply, while an apply
   cannot mutate a target whose immediate re-proof fails.

2. **MEDIUM — “CLI-only” cleanup has no defined admission boundary.**

   The dispatch adds `POST /api/vps-status/cleanup` but only says that it is
   “CLI-only”; it supplies neither an API dependency nor a CLI request identity
   that distinguishes the command from an ordinary same-origin browser request.
   Existing `planner.cli.http` sends the ordinary HTTP seam, and the current
   request-actor mechanism accepts unattributed direct requests. Omitting a UI
   button does not make the HTTP mutation CLI-only.

   **Correction:** state the intended authority precisely and implement/test it.
   If CLI-only means “not offered by the browser UI,” say that explicitly and
   test that no frontend call targets the cleanup route. If it means an admission
   boundary, define a server-enforced operator/CLI transport or identity that
   the command uses, reject ordinary browser requests, and add API and CLI tests
   for both the allowed and rejected paths. Do not treat a display-only frontend
   omission as mutation authority.

3. **MEDIUM — the snapshot contract and state policy are underspecified.**

   The plan promises stable top-level sections and the four UI states, but does
   not name the JSON fields, their nullable/unavailable representation, or the
   deterministic rules that turn evidence into healthy, warning, unavailable,
   and review-needed. In particular, “backup recency” and “disk pressure” lack
   concrete assessment criteria. This conflicts with the standing requirement
   for contract-first, typed shapes and concrete acceptance values, and makes it
   impossible to construct meaningful four-state fixtures without implementation
   guesses.

   **Correction:** add an immutable backend snapshot contract and pure
   assessment policy before collectors: exact section names/types, a single
   availability/status vocabulary, and explicit backup/disk criteria. Keep any
   tunable values in an isolated status-policy configuration rather than inline.
   Mirror that one response shape in `web/src/lib/types.ts`, and make unit and
   frontend fixtures use literal payloads for all four states.

4. **LOW — the release-root seam is described inaccurately.**

   The dispatch calls `PLAN_RELEASE_ROOT` “the validated release-root source in
   `resolve_application_root()`.” Current `resolve_application_root()` only
   expands and resolves that environment value; it does not validate a manifest.
   Validation occurs in `build_release_launch_env()` before the launcher sets the
   value. A collector that treats `resolve_application_root()` itself as release
   proof would overclaim identity.

   **Correction:** amend the plan to use `Config.release_sha` for the configured
   release identity, and, if filesystem release evidence is needed, validate the
   manifest explicitly through the existing release validator. Describe
   `PLAN_RELEASE_ROOT` as launcher-provided, not intrinsically validated.

5. **MEDIUM — the focused gate omits the test owner for a changed launch boundary.**

   The dispatch changes `src/planner/environments/release_launcher.py` to
   allowlist the new backup-directory variable, but the focused command omits
   `tests/unit/test_release.py`. That file owns
   `build_release_launch_env()` and currently asserts the launch environment’s
   allowlisting/scrubbing behavior. `test_config.py` cannot prove the release
   launcher carries the configured value without leaking unrelated environment
   values.

   **Correction:** add `tests/unit/test_release.py` to the focused pytest gate
   and add cases that the new `PLAN_*` backup-directory value is retained while
   unrelated values remain scrubbed. Also specify how the Playwright fixture
   produces each of the four snapshot states (for example, route-fulfilled
   sanitized API fixtures for rendering plus a real endpoint check); the current
   subprocess server fixture has no described collector injection seam.

## Confirmed direction

The header-popover direction, manual-fetch ownership outside the resource
catalogue, Mac resource unavailability, retention reuse, and the explicit
deletion exclusions are aligned with the contract. No broader status route,
polling, persistent preview model, database/event additions, process control,
or worktree cleanup should be introduced.

## Resolution review

**PASS.** All five findings are resolved by the revised dispatch and remain
consistent with the current contracts/source:

- Each dry-run or apply invocation builds one fresh in-memory inventory; apply
  re-proves targets immediately before mutation, so no prior preview authorises
  later work.
- Cleanup is a direct, host-local `panels environment cleanup` operation; no
  cleanup HTTP route is planned, and the SSH CLI collector does not depend on
  the Panels server.
- The immutable snapshot fields, state vocabulary, unavailable representation,
  and disk/backup policy thresholds are explicit.
- `Config.release_sha` is the release-identity source; `PLAN_RELEASE_ROOT` is
  correctly limited to launcher-provided path context unless separately
  manifest-validated.
- The focused gate includes `tests/unit/test_release.py` for the launch-env
  allowlist change. Repository-owned daily launchd/systemd inputs run a fresh
  apply invocation under the operator identity, without adding an in-process
  scheduler.
