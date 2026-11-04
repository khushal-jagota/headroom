# Skills

A skill is the standing instruction for a kind of agent: the shared Worker skill, a specialist skill for each Worker type, the Manager skill, the Chief skill, and a short orientation to Panels. Skills exist so that how an agent should work is written once, edited in one place, and read by every agent of that kind on any backend.

## What it is made of

- **The skill row.** Shared role text lives in the database. Structured preset guidance generates specialist skill rows.
- **The published copies.** Headroom writes each row out as a skill file whenever a database opens and whenever a skill is saved. Every Hermes, Codex, and Claude home links to those copies, and a server with an explicit workspace links it there too.
- **The seed.** The skill tree packaged with the code fills a database that has no skills yet. Nothing reads it after that.
- **Skill history.** The editor keeps saved versions in the database. Worker steps do not record skill versions.
- **The editor and the command.** One editor in Settings opens every role's skill. `panels skill list`, `show`, `edit`, and `apply` reach the same writer.

```
 editor or panels skill ──► skill row ──► published copy ◄── agent homes link here
                               ▲
 packaged seed (fresh database only)
```

## How it works

Khushal and the Chief stand above skills, so both may edit them. A Worker or a Manager that wants a skill changed hands the exact text to the owner or the Chief. A reviewed batch applies in one transaction. Specialist guidance changes through its structured preset editor.

The specialist reference saved on a Ticket decides which skill a preset Worker reads. A custom Ticket receives its assembled guide at kickoff. See [Worker types](worker-types.md). A saved skill reaches an agent the next time it loads the skill. A turn already running keeps what it read. Saving a skill sends no prompt, so the conversation notebook does not change.

`panels skill check` reports a skill that names a command, stage, or skill this build lacks. It reports and does not rewrite.

## Decisions that shape it

- Shared role rows and structured preset guidance are their respective sources. Specialist skills are generated output. Editing the packaged tree changes the next fresh install.
- One copy for all three backends, so the same kind of agent gets the same words on Hermes, Codex, and Claude.
- Agents that need a skill changed hand the text up. Only the owner and the Chief write skills.

## Code

- `src/planner/managed_skills.py`: rows and published copies. `src/planner/skill_versions.py`: saved history.
- `src/planner/worker_settings/`: the HTTP writers.
- `src/planner/skills/`: the seed.
- `web/src/components/RoleSkillEditor.svelte`: the editor.

_Last verified: 2026-11-04._
