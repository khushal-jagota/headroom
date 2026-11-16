import type { Database } from '../core/database/contracts.ts';
import { issue_launch_token } from '../agent_identity/data.ts';

/** Fixtures establish the current canonical association before this credential is issued. */
export function launch_headers(
  database: Database,
  conversation_id: string,
): { authorization: string } {
  return { authorization: `Bearer ${issue_launch_token(database, conversation_id)}` };
}
