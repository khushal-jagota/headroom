# Panels skill provisioning and slash-menu discovery

Session learning from 2026-07-10.

Panels role skills are intended to be native Hermes skills, not hardcoded slash-menu entries.

## Current architecture

- Panels uses the active Hermes home, normally the user's `~/.hermes` unless
  `PLAN_HERMES_HOME` is explicitly set.
- Server startup provisions Panels-owned role skills from `src/planner/skills/` into that home as symlinks:
  - `~/.hermes/skills/panels -> src/planner/skills/panels`
  - `~/.hermes/skills/panels-worker -> src/planner/skills/panels-worker`
  - `~/.hermes/skills/panels-chief-of-staff -> src/planner/skills/panels-chief-of-staff`
- Hermes discovers those symlinked directories as normal local skills.
- Panels' web `/` menu is a UI bridge over Hermes discovery: the backend asks the shared gateway for `commands.catalog`, then the web composer renders the returned slash commands and skills.
- Running a slash skill from Panels uses Hermes command dispatch into the target session; it is not a separate Panels-only skill execution path.

## Debugging checklist

When a Panels role skill appears missing from slash commands or unavailable to a worker/Chief session:

1. Check the active home: by default `HERMES_HOME` is the user's `~/.hermes`, but an
   explicit `PLAN_HERMES_HOME` override can point elsewhere.
2. Run `hermes skills list` under that home and look for `panels`, `panels-worker`, and `panels-chief-of-staff`.
3. Check symlinks under that home's `skills/` directory and their targets under
   `src/planner/skills/`.
4. If the skill exists in Hermes but not in the web menu, check the shared gateway catalog path (`commands.catalog`) and the Panels command-catalog cache before changing UI code.
5. If the skill is discoverable but not automatically used, distinguish discovery from role preloading: worker/Chief role selection comes from launch/session environment such as `HERMES_TUI_SKILLS`, not merely from skill existence.

## Edit the canonical skill source

Worker and Chief skill edits always write the version-controlled package under
`src/planner/skills/<skill-name>/`. The database-side `worker-settings` directory
stores launch and ownership settings only; any legacy `SKILL.md` or `.candidates`
files there are not authoritative and are never read by a backend.

Provisioning links each Hermes-home skill directory to that canonical source. To
add a new skill, add its complete package to `src/planner/skills/`, register its
name in the canonical provisioning list, and verify the provisioned target is a
symlink to the source package.

When scripting several file writes, inspect every tool result for an `error` and verify the resulting paths. A wrapper process exiting successfully does not prove its nested writes succeeded.

## Pitfall

Do not add Panels role skills directly to a hardcoded frontend slash list to fix discoverability. Fix provisioning, active `HERMES_HOME`, gateway catalog, or role preloading depending on where the chain breaks.
