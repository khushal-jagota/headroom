# Packaged skills

These files seed a database that holds no skills. Nothing reads them again.

The authority is the `managed_skills` row in the running database. Panels writes
`data/skills/<name>/SKILL.md` from that row, and each agent home links to the copy. So an
edit here changes a fresh install only, and no running agent will read it.

To change what a running agent reads, write the exact text and hand it to the owner or the
Chief. Every skill write is owner-only.

Add a file here when a new Worker type ships, so a fresh install starts with its specialist
skill. `tests/unit/test_canonical_skill_sources.py` checks that.
