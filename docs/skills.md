# Skills

A skill is the standing instruction for a kind of agent: the shared Worker skill, a specialist skill for one Worker type, the Manager skill, the Chief skill. The row in `managed_skills` is the authority. What the agent reads on disk is a copy Headroom writes from that row.

## Why the row wins

On every database open, Headroom rewrites `data/skills/<name>/SKILL.md` from the rows. Hermes, Codex, and Claude homes link to those copies. The packaged tree at `src/planner/skills/` is only the seed for a database that has no skills yet. Nothing consults the package again. Editing a file in the package changes the next fresh install. It does not change a running agent.

History of skill text is kept with the database. A Worker step records which skill versions it was bound to, so a later reading can see what that step was instructed with. Those history writes are among the commits that stay quiet on the change signal, because no screen is waiting on them as live product state.

## Who may change one

Khushal and Chief stand above skills, so both may edit them. A Worker or a Manager that wants a live skill changed hands the exact text to the owner or the Chief. They do not write the row themselves. `PATCH /api/skills/<name>` updates one skill. `POST /api/skills/apply` applies a reviewed batch in one transaction, each entry named with the full text and the hash the editor expected. A stale hash is refused. If the row commits and the file copy fails, the error says so. Read the current skill and retry. The committed row is not rolled back by a later copy failure.

`panels skill check` and the stale-skill read report a row that names a command, stage, or skill this build lacks. They report. They do not rewrite.

## What a skill feeds

The copies feed every agent home that links to them. The specialist name on a Worker type decides which skill a Worker step binds. Publishing updates the copy the agent homes link to. A turn already in flight has whatever it already read. The next load of the skill reads the published copy. The conversation notebook does not gain a row merely because the skill row changed. Workers that must change a skill hand the exact text to the owner or Chief.

## Where it shows up

Settings → Config, and the Config tab on Usage. Chief, Manager, the shared Worker skill, and each Worker type’s specialist open in the same editor. `panels skill list`, `show`, `edit`, and `apply` are the command-line door.

## Pointers

`src/planner/managed_skills.py` publishes the copies. The HTTP writers are in `src/planner/worker_settings/`. The editor is `web/src/components/RoleSkillEditor.svelte`.

_Last verified: 2026-10-30._
