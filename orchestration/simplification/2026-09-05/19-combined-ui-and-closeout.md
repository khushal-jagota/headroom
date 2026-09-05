# Combined app and closeout

The root exercised the combined production build against a private local database,
with fake backends and dispatch disabled. Four fictional Tickets, two Outcomes and
two Sprints were created through canonical writers; the completed Ticket progressed
through real proposal and acceptance decisions using the existing fixture helper.
No live Panels rows were written and no real provider conversation was started.

The actual Sprint screen showed the empty committed Outcome and a 1/3 Ticket progress
count. Carry opened with no Tickets selected and excluded completed work. Selecting
one unfinished Ticket and Sprint B moved exactly that Ticket. Sprint A retained its
completed Ticket, unselected work and both commitments, with progress changing to
1/2. The Outcome workspace kept its brief and displayed commitments to both Sprints,
with each child labeled by its own Sprint or Backlog. Its historical Sprint link
opened the explicit Sprint A tracking screen.

The root made one small wording repair during this check: the disclosure containing
all Tickets outside Today, including completed history, now says Other Tickets
instead of Remaining Tickets. The independent reviewer inspected that line and
retained approval in report 17. No new test is appropriate for this text-only change.

The three substantive UI repair proofs are the existing mocked-browser harnesses
listed in report 17. This local journey supplements those assertions; screenshots
alone are not the behavioral gate. No new live-server E2E matrix was added.

Before final verification, fetched `origin/staging` was still
`d55260b3de6e88b8d69275e19f9c3c244ef21030`, already an ancestor of this tree.
The protected conversation, conversation-start, message delivery, worker context,
backend adapters and frontend conversation directories have an empty diff against
the original fetched baseline `b7ca8e047967e405feeebe058f5cd2ef82b2c2e5`.
The original untracked uv.lock still has SHA-256
`9265b9678c009c5c8a5c032fb7ac7e0c4cd3223890bdd2c4622526ebe82ca929`.

The first final verification passed Ruff, mypy over 359 files, build checks,
frontend checks (338 including typecheck cases plus 11 standalone scripts), and
all eight integration cases. Ten unit fixtures and three file-preview E2E fixtures
still described removed storage or planning semantics. The six-file test-only
repair preserves the existing cases, uses canonical preview setup, and proves the
new migration mapping without weakening preservation assertions. Focused checks
passed 41 unit cases and all four file-preview E2E cases; the independent reviewer
approved the repair in report 17. No production implementation changed.

The comparable inventory remains 1,155 cases (53.9% removed), including 19 E2E.
The failed run is retained privately as
`data/simplification-deeper/verify-deeper-attempt-1.log` until cleanup. A clean final
repository verification and staging closeout remain pending below.
