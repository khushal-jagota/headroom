/** Local physical custody, Git preservation, and durable workspace retirement. */
export { workspace_identity, read_workspace_identity } from './filesystem/shared.ts';
export { repository_root, prepare_workspace } from './filesystem/preparation.ts';
export {
  workspace_ticket_owners,
  reason_to_keep,
  cleanup_ticket_folder,
} from './filesystem/safety.ts';
export {
  cleanup_local_workspaces,
  prepare_workspace_retirement,
  retire_workspace,
  detach_workspace,
  retry_retired_workspaces,
} from './filesystem/retirement.ts';
