# ACP-05 compacted replay correction plan review

## Verdict

**NOT READY.**

## Blockers

1. **The schema-version ownership and direct-upgrade path are not frozen.** The plan adds and backfills
   `compaction_boundaries_json` at lines 86-88 but does not say whether `SCHEMA_VERSION` changes. ACP-06
   already owns v25 and, for every incoming version below 25, explicitly skips
   `_migrate_conversation_session_bindings`; `CREATE TABLE IF NOT EXISTS` cannot add the column to an
   existing binding table. As written, implementation can either consume ACP-06's reserved version or
   leave a direct pre-column -> v25 upgrade with a binding table that lacks a column required by all
   later compactions. Freeze this as an amendment to the still-unlanded v24 schema with **no version
   bump**, keep an idempotent add/backfill for existing dogfood v24 databases, and amend ACP-06's v25
   contract/plan so its terminal transaction produces/preserves the column even when upgrading directly
   from a pre-column database while still deleting the old binding rows. Add v24 dogfood reopen and
   pre-column -> v25 direct-upgrade proofs.

2. **The compaction CAS does not define accumulated provenance for a second compaction.** Lines 95-103
   say that compaction CAS writes the supplied active-boundary tuple, but never combine it with the exact
   expected binding's already persisted tuple. Replacing the column with only the current capture loses
   the first boundary on the second N -> N+1 transition, contradicting lines 32-37 and the stable-first-ID
   acceptance at lines 80-82. Define the compaction CAS to validate the exact expected row and its
   provenance under the same `BEGIN IMMEDIATE`, append the current broker boundaries in order, enforce
   uniqueness across the combined tuple, and write that combined value with the candidate binding and
   product mirror. A losing or ambiguous CAS must return/read only the durable winner's exact tuple and
   must not append the loser's boundaries. Prove two sequential compactions, multiple boundaries in one
   capture, ambiguous success, and an external winner.

3. **Replay classification has two conflicting owners.** Lines 49-52 declare
   `CompactionCaptureTransition` and the hub transition ports already classified as
   `ConversationReplayItem`, while lines 148-160 require the hub helper to call `classify_replay` for
   that same compaction replay. One interpretation classifies twice; the other passes raw replay through
   a contract that says it is classified. Choose one owner. The current source shape supports keeping
   every child/registry replacement replay raw through the runtime and transition ports, then making the
   hub's batch helper the sole classifier for ordinary attach/refresh/respawn/restart,
   requested-cancel replacement, normal compaction, and external-winner adoption. Only that helper's
   result should use `ConversationReplayItem`. Add a classifier-spy assertion for exactly one call per
   replay batch alongside the exact-position/no-raw-summary assertions.

No implementation files were edited and `./verify` was not run.
