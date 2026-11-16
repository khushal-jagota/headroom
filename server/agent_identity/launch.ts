/** Credentials enter only the transient start passed to a local native child. */
import type { Database } from '../core/database/contracts.ts';
import type { BackendChild } from '../conversation/backends/contracts.ts';
import { ConversationRoleMaterials, ResolvedConversationStart } from '../conversation/contracts.ts';
import { LAUNCH_TOKEN_ENVIRONMENT_NAME, UNIDENTIFIED_LAUNCH_TOKEN } from './contracts.ts';
import { canonical_principal, issue_launch_token } from './data.ts';

export function local_launch_start(
  database: Database,
  start: ResolvedConversationStart,
): ResolvedConversationStart {
  const principal = database.read((sql) => canonical_principal(sql, start.conversation_id));
  const token =
    principal === null
      ? UNIDENTIFIED_LAUNCH_TOKEN
      : issue_launch_token(database, start.conversation_id);
  const environment = new Map(start.role_materials?.identity_environment_variables ?? []);
  environment.set(LAUNCH_TOKEN_ENVIRONMENT_NAME, token);
  return new ResolvedConversationStart({
    ...start,
    role_materials: new ConversationRoleMaterials({
      role_text: start.role_materials?.role_text ?? '',
      identity_environment_variables: [...environment],
    }),
  });
}

export function authenticated_local_child(child: BackendChild, database: Database): BackendChild {
  return new Proxy(child, {
    get(target, name) {
      if (name === 'start')
        return (
          start: ResolvedConversationStart,
          options: { vendor_session_cursor: string | null },
        ) => target.start(local_launch_start(database, start), options);
      const value = Reflect.get(target, name, target);
      return typeof value === 'function' ? value.bind(target) : value;
    },
  });
}
