# Skills

A skill is the standing instruction for a kind of agent: the shared Worker skill, a specialist skill for each Worker type, the Manager skill, the Chief skill, and a short orientation to Panels. Skills exist so that how an agent should work is written once, edited in one place, and read by every agent of that kind on any backend.

## What it is made of

- **The skill row.** Shared role text lives in the database. Structured preset guidance generates specialist skill rows.
- **The published copies.** Headroom writes each row to its own skill folder when a database opens or a skill changes. Machine code makes its copies from those rows and links provider account homes to them. Only machine code opens those homes. On the local link, it removes the colocated server's old links before its first publication.
- **The seed.** The skill tree packaged with the code fills a database that has no skills yet. Nothing reads it after that.
- **Skill history.** The editor keeps saved versions in the database. Worker steps do not record skill versions.
- **Supporting files.** A skill folder can hold other files beside `SKILL.md`. They live only on disk: no row holds them, the skills backup copies them, and republication leaves them alone.
- **The editor and the command.** One editor in Settings opens every role's skill. `panels skill list`, `show`, `edit`, `apply`, `delete`, and `write-file` reach the same owner-only writer.

```
 editor or panels skill ──► skill row ─┬─► published copy ◄──── workspace links here
                               ▲       └─► machine code copy ◄── agent homes link here
 packaged seed (fresh database only)
```

## How it works

The owner and the Chief stand above skills, so both may edit them. `panels skill delete` removes a skill's row and its whole folder, and refuses a role skill and any skill that a Worker type uses. Tickets use their saved guide after their specialist skill is deleted. Machine code removes the links of a deleted skill from agent homes when it next starts a Ticket. `panels skill write-file` writes a supporting file inside a skill folder, and never the skill's own `SKILL.md`. A Worker or a Manager that wants a skill changed hands the exact text to the owner or the Chief. A reviewed batch applies in one transaction. Specialist guidance changes through its structured preset editor.

Every Worker follows its own Ticket's workflow guide, which it receives at kickoff. A specialist skill is the current guide of its preset, for reading and editing. It does not reach a Ticket made earlier. See [Worker types](worker-types.md). A saved skill reaches an agent the next time it loads the skill. A turn already running keeps what it read. Saving a skill sends no prompt, so the conversation notebook does not change.

`panels skill check` reports a skill that names a command, stage, or skill this build lacks. It reads the command tree that the CLI itself declares, so it cannot fall behind the commands. It reports and does not rewrite.

## Decisions that shape it

- Shared role rows and structured preset guidance are their respective sources. Specialist skills are generated output. Editing the packaged tree changes the next fresh install.
- One copy for every backend, so the same kind of agent gets the same words on Codex, Claude and Antigravity.
- Agents that need a skill changed hand the text up. Only the owner and the Chief write skills.

## Code

- `server/skills/`: rows, published copies, saved history (`data.ts`), and the check (`staleness.ts`).
- `server/worker_settings/`: the HTTP writers.
- `server/skills/seeds/`: the seed, read by `server/skills/data.ts`.
- `web/src/components/settings/SettingsSkillSections.svelte`: the editor, on the Settings Agents pages.

_Last verified: 2026-11-23._
