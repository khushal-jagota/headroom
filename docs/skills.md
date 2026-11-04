# Skills

A skill is the standing instruction for a kind of agent: the shared Worker skill, a specialist skill for each Worker type, the Manager skill, the Chief skill, and a short orientation to Panels. Skills exist so that how an agent should work is written once, edited in one place, and read by every agent of that kind on any backend.

## What it is made of

- **The skill row.** The text of each skill lives in the database. The row is the authority.
- **The published copies.** Headroom writes each row out as a skill file whenever a database opens and whenever a skill is saved. Every Hermes, Codex, and Claude home links to those copies, and a server with an explicit workspace links it there too.
- **The seed.** The skill tree packaged with the code fills a database that has no skills yet. Nothing reads it after that.
- **Skill history.** Each saved version is kept, and each Worker step records which versions it was sent with, so a later reader can see what that step was instructed to do.
- **The editor and the command.** One editor in Settings opens every role's skill. `panels skill list`, `show`, `edit`, and `apply` reach the same writer.

```
 editor or panels skill ──► skill row ──► published copy ◄── agent homes link here
                               ▲
 packaged seed (fresh database only)
```

## How it works

Khushal and the Chief stand above skills, so both may edit them. A Worker or a Manager that wants a skill changed hands the exact text to the owner or the Chief. Edits are revision-checked, and a reviewed batch applies in one transaction.

The specialist named on a [Worker type](worker-types.md) decides which skill a Worker step uses. A saved skill reaches an agent the next time it loads the skill. A turn already running keeps what it read. Saving a skill sends no prompt, so the conversation notebook does not change.

`panels skill check` reports a skill that names a command, stage, or skill this build lacks. It reports and does not rewrite.

## Decisions that shape it

- The row wins. Editing the packaged tree changes the next fresh install, not a running agent.
- One copy for all three backends, so the same kind of agent gets the same words on Hermes, Codex, and Claude.
- Agents that need a skill changed hand the text up. Only the owner and the Chief write skills.

## Code

- `src/planner/managed_skills.py`: rows and published copies. `src/planner/skill_versions.py`: history and step bindings.
- `src/planner/worker_settings/`: the HTTP writers.
- `src/planner/skills/`: the seed.
- `web/src/components/RoleSkillEditor.svelte`: the editor.

_Last verified: 2026-11-04._
