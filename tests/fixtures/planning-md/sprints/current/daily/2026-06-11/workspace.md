# Workspace

Date: 2026-06-11

## Necessary Calls

- Pick one lane for the day and hold it.
  - Recommended call: finish the import pipeline before touching polish.

## Tickets

- Sketch the onboarding survey.
  - Ticket ID: ticket-20260611-onboarding-survey
  - Readiness: Concepts
  - Mode: Manual
  - Priority: P2
  - Project: Vylo
  - Body: Work out what the survey must learn before any UI is sketched.
  - Current state: nothing exists yet.
  - Next: list the three decisions the survey feeds.

- Shape the export format.
  - Ticket ID: ticket-20260611-export-format
  - Chat ID: 20260611_090000_abc123
  - Readiness: Needs Shaping
  - Priority: P1
  - Project: Tribe
  - Success: a one-page format note that a second reader can implement from.

- Cut the release branch.
  - Ticket ID: ticket-20260611-release-branch
  - Readiness: Ready
  - Priority: P0
  - Project: Vylo
  - Success: the branch exists and CI is green on it.
  - Approach: branch from main after the fixture tests pass, then tag.

- Build the import pipeline.
  - Ticket ID: ticket-20260611-import-pipeline
  - Readiness: In Progress
  - Mode: Paired
  - Priority: P0
  - Project: Vylo
  - Body: Parse the fixture tree and land rows behind one transaction.
  - Success: the fixture import passes twice with zero duplicates.
  - Boundary: importer only; no CLI wiring yet.
