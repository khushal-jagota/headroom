---
name: "panels-worker-amend-worker"
description: "Stage-by-stage guidance for an amend-worker ticket \u2014 changing or removing a Worker type."
---

This Ticket changes or removes one Worker type. One type per Ticket.

A Worker type is a preset. A Ticket copies the preset's stages, fields, guidance and launch setup when it is made, and keeps that copy. Its Worker follows the Ticket's own guide. So an amendment reaches Tickets made after the save, and no Ticket that already exists.

Structural change is the normal case: a Stage is removed or added, or an ownership mode, a launch default or the guidance changes. Removal is the most common, and it is usually correct. A Stage earns its place, and two Stages that are always approved together are one Stage. Do not argue the user out of a removal.

Small guidance edits are not this Worker's job. The user makes those in the preset editor in Settings.

## What this Worker cannot change

- A custom workflow. It belongs to one Ticket and has no type.
- The stages of a Ticket that already exists. No command changes a Ticket's workflow after it is made. If a live Ticket must follow the new shape, a new Ticket replaces it. The user decides that.

## Where a type lives

A type is one database row. A save or a delete takes effect at once.

- `panels worker-type show <type>` prints the record, in the shape that `panels worker-type save` takes back on stdin.
- The record holds the guidance and the instructions for each Stage. A save writes the specialist skill from them. Panels refuses a skill body sent any other way. Only the skill description is a separate write: `panels worker-type skill <type> --description "..."`.
- `panels worker-type delete <type>` removes a type. It refuses Coding, because the board lays out its columns in the order of Coding. It also refuses a type with an unfinished Ticket that saved no model, and names those Tickets.

You can read all of this. You cannot write any of it. These doors are owner-only, and no Ticket principal can reach them. That is deliberate. So you prepare the exact bytes and hand them up. Complete the owner-only actions during the final Drafting review.

`server/worker_types/shipped_worker_types.json` and `server/skills/seeds/` seed only a database that has no types or skills. A live row and a shipped one can differ, and the live row wins.

## Boundaries

- One type per Ticket. If the change clearly belongs on a second type too, name it and stop.
- Repair only what is stuck.

## Amendment

Agree the change and its blast radius with the user. Read the current record with `panels worker-type show <type>` before you say anything.

Then report what the change does to the Tickets of the type. `panels ticket list --worker-type <type> --all --json` names them and where they sit. Give the count and the ids. "Some Tickets are affected" is not a blast radius.

- **Unfinished Tickets of the type** keep their own stages and guidance. The amendment does not reach them. Say for each whether that is acceptable, or whether the user wants a replacement Ticket.
- **For a removal**, new Tickets of the type are refused after the delete. Name each unfinished Ticket that saved no model, because the delete refuses while one exists. The user decides what happens to each: a saved model, done, or deleted.
- **Finished Tickets** keep their record. Do not rewrite history.

The user decides what happens to anything in the way. Put that decision in this field.

## Drafting

Write the revised record as a Ticket artifact, in the exact shape that `panels worker-type show` prints. For a removal, the artifact is the delete command. The owner or Chief reviews the exact artifact before any live write.

The exact stages get read before they take effect, not after. Show what changed, not the whole record, when the change is small.

Guidance is instruction for a capable agent, not documentation. Amending it is more often deleting than adding. Do not start the guidance with a top heading, and do not repeat a Stage name as a heading in its instructions. Panels adds both.

### Final Drafting review

The writes are not yours to make. Hand them to the owner or Chief during this review. They review the exact content before they run the approved commands.

1. Prove the record before you hand it over. Validate it with the code that runs live, without a database and without writes to the installed tree:

   ```sh
   node --input-type=module -e '
   const app = "/home/vps/Deployments/Panels/current/app/server/worker_types";
   const { readFileSync } = await import("node:fs");
   const { definition_from_json } = await import(app + "/store.ts");
   const { validate_definition } = await import(app + "/registry.ts");
   const { render_workflow_guide } = await import(app + "/guidance.ts");
   const [file, ...skills] = process.argv.slice(1);
   const record = definition_from_json(readFileSync(file, "utf8"));
   validate_definition(record, new Set(skills));
   process.stdout.write(render_workflow_guide(record));' record.json $(panels skill list)
   ```

   It prints the skill body that the save will store. Keep that body as an artifact too.
2. Hand your supervisor, or the Chief if you have none, the exact commands in order:
   - `panels worker-type save` with the approved record on stdin. This also writes the specialist skill.
   - `panels worker-type skill <type> --description "..."`, only when the description changes.
   - For a removal, `panels worker-type delete <type>` instead.
3. Give the sha256 of each artifact, so whoever runs it can confirm they ran what was approved, and so you can confirm it afterwards.
4. Read it back yourself with `panels worker-type show <type>` and `panels skill show <skill>`. After a removal, `show` must return not found. The owner or Chief accepts Drafting only after this readback verifies the approved change.
5. After a removal, the specialist skill stays. `panels skill delete <skill>` can remove it after no Worker type uses it. Existing Tickets keep their saved guide. Hand that delete to the owner or Chief.

## Consequences

Bring the repository in line through the ordinary branch and staging route. This has no effect on this Panels.

- Update the entry for the type in `server/worker_types/shipped_worker_types.json`, or remove it.
- Update `server/skills/seeds/panels-worker-<name>/SKILL.md`, or remove it. A fresh install converts it into the guidance and the Stage instructions, so keep one heading per Stage.
- Update `docs/worker-types.md` when the set of types or their shape changed.

A seeding pass on a fresh deploy does not apply your amendment: seeding declines a table that already holds rows. Only the explicit write does it.

A good Consequences is short and verified: what changed, what the live store reports now, who ran the write, and what repository work finished. Submit the report with `panels ticket propose`. If the ceiling is Done, the report settles automatically. Otherwise the ceiling holder reviews it. If authorized follow-through cannot finish, request help instead of reporting completion.
