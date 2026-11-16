# Temporary catch-up gaps

Retained cases: 0.

The second parity PR closes the seven temporary merge-checkpoint entries. Their original failure evidence remains in the checkpoint commits and hosted runs [43904665539](https://github.com/khushal-jagota/headroom/actions/runs/43904665539) and [43910025587](https://github.com/khushal-jagota/headroom/actions/runs/43910025587).

Closed cases now run without expected-failure marks:

- `tests/compare/direct/test_unit20_domain_cli.py::test_unit20_retained_dataset_cli`: Consequences: Ticket list still emits removed waiting_to_closeout; observed at line377 in hosted run43910025587; second PR removes this projection
- `tests/compare/direct/test_unit20_domain_cli.py::test_unit20_worker_cleanup_cli`: restart cleanup: fresh executor evidence and distinct local/remote proof refusals differ
- `tests/compare/direct/test_unit21_backups.py::test_unit21_backup_v2_cross_language_wal_roots_and_retention`: Consequences: restored workflow ceiling_range still includes Done on TypeScript
- `tests/compare/direct/test_unit21_interruption_capture.py::test_unit21_fixed_restore_entry_preserves_populated_snapshot`: Consequences: restored workflow ceiling_range still includes Done on TypeScript
- `tests/compare/direct/test_unit21_ticket_scratch.py::test_scratch_git_work_and_fresh_apply_checks_match_python`: workspace: linked-worktree fixture and TypeScript preparation do not yet match independent clone retirement
- `tests/compare/direct/test_unit22_inbox_consumers.py::test_unit22_inbox_canonical_consumers`: Consequences: canonical write and replay workflow ceiling_range still includes Done
- `tests/compare/direct/test_worker_runtime_parity.py::test_worker_runtime_parity`: workspace: persisted identity_environment_variables lack PLAN_TICKET_PROJECT_ID

The private launch-token port remains separately expected-open. No other comparison gap is admitted.
