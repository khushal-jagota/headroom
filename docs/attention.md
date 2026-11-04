# Historical attention

The attention API answers where Khushal’s recorded involvement went between two dates. Chief can compare Projects, then inspect the work behind one Project.

```sh
panels attention summary --first-date 2026-10-29 --last-date 2026-11-04 --group-by project --json
panels attention summary --first-date 2026-10-29 --last-date 2026-11-04 --project PROJECT_ID --group-by ticket --json
```

The HTTP equivalent is `GET /api/attention/summary`. Its query accepts `first_date`, `last_date`, `group_by`, `scope_kind`, `scope_id`, `sort`, `limit`, and `offset`.

Dates are inclusive Europe/Berlin dates. Without dates, the API selects the last seven local dates. It compares the selected range with the preceding range of equal length. Today ends at the snapshot time and remains partial.

`group_by` selects Project, Manager, Ticket, or goal. Scope selects all work, one Project, one Manager, one Ticket, one goal, or Chief. Group and scope are independent. The CLI uses `--project`, `--manager`, `--ticket`, `--goal`, or `--chief` for the scope. Select only one.

Each group contains owner messages, explicit owner approvals, elapsed approval waits, and the share of active Tickets without owner involvement. It also contains the preceding period, differences, message share, and approval share. Titles and links identify the work. `sort` accepts `owner_messages`, `owner_approvals`, or `approval_wait_seconds`. Pagination never changes totals or share denominators.

Owner messages use authenticated sender metadata. Agent prompts and replay duplicates do not count as owner messages. Approval counts exclude automatic acceptance and agent approval. Active Tickets include recorded agent activity, so work without owner involvement remains visible.

Gate waits describe elapsed delays within the selected range. Overlapping gates produce summed waits and a separate union of elapsed time. Neither number measures hours of owner attention. Open gates contribute elapsed history through the snapshot, without a current-demand recommendation.

Each figure states its coverage and limits. Older approval and gate history can be unknown. Missing history stays null, not zero. A share without owner involvement remains unknown when its evidence cannot establish absence.

Goal figures cover direct goal conversations. Panels has no Ticket-to-goal membership. Other work remains unattributed. Shared Project figures provide context and never enter direct goal totals.

These reads do not report current demands or forecast capacity. Panels cannot see reading time or work on the Mac. Chief asks Khushal about those.

## Retained daily figures

The existing measure-source runtime can retain the same calculator’s daily results. A setup helper creates one source responsible to Chief, three datasets, and three scalar measures. Each output covers owner messages, explicit approvals, or elapsed approval waits.

After installation, Chief or an authorized operator can configure collection through the CLI:

```sh
panels attention setup-collection --project PROJECT_ID --database-path /absolute/server/path/planner.db --json
```

The explicit path names the database on the Panels server. Use the server’s configured database path. The script opens it read-only and reports a real collection failure if it is unavailable. The command reuses its existing source and outputs on repeated calls. It uses existing source authority, script revision, and schedule APIs. The default schedule is 00:05 in the server’s local timezone. `--local-time HH:MM` selects another time.

The saved script opens that database read-only. It never selects a database from inherited environment variables or upgrades a schema. Normal collection records yesterday and today, with today partial. A requested history range records those explicit dates. Source receipts retain query bounds, limitations, and provenance.

Datasets separate metrics so unknown approvals cannot erase known message counts. Their dimensions preserve destination, Project, Manager, attribution basis, and coverage. Unknown values stay absent from numeric rows with an explicit limitation. Scalar measures retain nulls for unknown totals.

The setup result identifies the source, dataset, and measure IDs. Use existing `panels dataset read` and `panels measure read` commands for retained results. Existing goal charts can bind the scalar measures. Range summaries use original facts, because daily distinct counts cannot be added to obtain range counts.

Code: `src/planner/attention/`.

New automatic prompts carry a trusted system marker from their original server admission. Public messages retain their authenticated sender, regardless of their display label. Legacy prompts without sender or system provenance keep unknown coverage. A queued prompt preserves its original provenance.

A deleted Ticket can retain its title through an approval or gate snapshot. That snapshot does not establish the Project or Manager for another message. Such messages retain Ticket identity, but their missing relationships remain unattributed.
