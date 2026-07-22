# Panels skill provisioning and slash-menu discovery

Session learning from 2026-07-10.

Panels role skills are intended to be native Hermes skills, not hardcoded slash-menu entries.

## Current architecture

- Panels uses a Panels-specific Hermes home, normally `data/hermes-home`.
- Server startup provisions repo role skills from `skills/` into that home as symlinks:
  - `data/hermes-home/skills/panels -> skills/panels`
  - `data/hermes-home/skills/panels-worker -> skills/panels-worker`
  - `data/hermes-home/skills/panels-chief-of-staff -> skills/panels-chief-of-staff`
- Hermes discovers those symlinked directories as normal local skills.
- Panels' web `/` menu is a UI bridge over Hermes discovery: the backend asks the shared gateway for `commands.catalog`, then the web composer renders the returned slash commands and skills.
- Running a slash skill from Panels uses Hermes command dispatch into the target session; it is not a separate Panels-only skill execution path.

## Debugging checklist

When a Panels role skill appears missing from slash commands or unavailable to a worker/Chief session:

1. Check the active home: `HERMES_HOME` may be `data/hermes-home`, not the user's default `~/.hermes`.
2. Run `hermes skills list` under that home and look for `panels`, `panels-worker`, and `panels-chief-of-staff`.
3. Check symlinks under `data/hermes-home/skills/` and their targets under repo `skills/`.
4. If the skill exists in Hermes but not in the web menu, check the shared gateway catalog path (`commands.catalog`) and the Panels command-catalog cache before changing UI code.
5. If the skill is discoverable but not automatically used, distinguish discovery from role preloading: worker/Chief role selection comes from launch/session environment such as `HERMES_TUI_SKILLS`, not merely from skill existence.

## Promote a managed skill into repo source

A skill under `data/hermes-home/skills/<category>/...` may be runtime-only procedural memory rather than a repo-owned Panels skill. When the user chooses to make one durable and shared:

1. Confirm its provenance first: inspect whether it is tracked, a symlink, or a regular managed directory. Do not imply the user authored it when history proves only that agents used it.
2. Copy the complete skill package—`SKILL.md` plus `references/`, `templates/`, and `scripts/`—into the repo's normal flat source at `skills/<skill-name>/`.
3. Add the name to Panels' canonical provisioning/discovery list so future homes receive it. A source directory alone is not proof that startup exposes it.
4. Verify the repo package is complete before deleting the managed copy. Then remove the old regular directory and run the normal provisioner so the Hermes-home target becomes a symlink to repo source. Provisioning may deliberately leave an existing regular directory untouched, so replacement order matters.
5. Verify both layers: source and former managed package have identical contents, the runtime target is a symlink to the repo directory, and focused provisioning tests pass.

When scripting several file writes, inspect every tool result for an `error` and verify the resulting paths. A wrapper process exiting successfully does not prove its nested writes succeeded.

## Pitfall

Do not add Panels role skills directly to a hardcoded frontend slash list to fix discoverability. Fix provisioning, active `HERMES_HOME`, gateway catalog, or role preloading depending on where the chain breaks.
