/** Pure resolution of Worker, Chief, and Manager launch layers. */
import { PlannerError } from '../../core/contracts.ts';
import { ConversationRoleMaterials, FLOOR_DEFAULT_ACCESS } from '../../conversation/contracts.ts';
import type {
  ConversationAccess,
  ConversationBackendKey,
  WorkspacePathPolicy,
} from '../../conversation/contracts.ts';
const ROLE_DIRECTIVE_PREFIX =
  'Start with the `panels` skill. It explains the system and is necessary, then drill through to your identity through the skills layers.';
export const WORKER_ROLE_TEXT = `${ROLE_DIRECTIVE_PREFIX} You are a ticket worker.`;
function home_folder_text(shared_workspace_folder: string, extra = ''): string {
  return (
    " Your start folder is your own. Use it to keep things for yourself: notes, scratch files, and rules you want to remember. Don't copy what is already recorded in Tickets or elsewhere in Panels." +
    extra +
    ` The repositories are under ${shared_workspace_folder}.`
  );
}
export function chief_role_text(shared_workspace_folder: string): string {
  return (
    `${ROLE_DIRECTIVE_PREFIX} You are a chief of staff.` + home_folder_text(shared_workspace_folder)
  );
}
export function manager_role_text(shared_workspace_folder: string): string {
  return (
    `${ROLE_DIRECTIVE_PREFIX} You are a Manager.` +
    home_folder_text(
      shared_workspace_folder,
      ' Keep private notes out of artifacts/. The owner sees those files. Read `panels manager workspace "$PLAN_MANAGER_ID"` to identify your visible page and its edit command.',
    )
  );
}
export interface ConversationStartConfiguration {
  backend_key: ConversationBackendKey;
  model: string;
  reasoning_effort: string | null;
}
export interface ConversationStartOverrides {
  backend_key?: ConversationBackendKey | null;
  model?: string | null;
  reasoning_effort?: string | null;
  reasoning_effort_is_set?: boolean;
}
export const NO_CONVERSATION_START_OVERRIDES: Readonly<ConversationStartOverrides> = Object.freeze({
  backend_key: null,
  model: null,
  reasoning_effort: null,
  reasoning_effort_is_set: false,
});
export function overrides_for_existing_conversation(
  overrides: ConversationStartOverrides,
): ConversationStartOverrides {
  return overrides.backend_key != null && overrides.model == null
    ? { ...overrides, backend_key: null }
    : overrides;
}
export interface ConversationStartValues extends ConversationStartConfiguration {
  role_materials: ConversationRoleMaterials;
  workspace_folder: string;
  access: ConversationAccess;
  workspace_path_policy: WorkspacePathPolicy;
}
export function worker_conversation_role_materials(ticket_id: string): ConversationRoleMaterials {
  return new ConversationRoleMaterials({
    role_text: WORKER_ROLE_TEXT,
    identity_environment_variables: [
      ['PLAN_ACTOR', 'worker'],
      ['PLAN_TICKET_ID', ticket_id],
    ],
  });
}
export function chief_conversation_role_materials(
  shared_workspace_folder: string,
): ConversationRoleMaterials {
  return new ConversationRoleMaterials({
    role_text: chief_role_text(shared_workspace_folder),
    identity_environment_variables: [['PLAN_ACTOR', 'chief']],
  });
}
export function manager_role_materials(
  manager_id: string,
  shared_workspace_folder: string,
): ConversationRoleMaterials {
  return new ConversationRoleMaterials({
    role_text: manager_role_text(shared_workspace_folder),
    identity_environment_variables: [
      ['PLAN_ACTOR', 'manager'],
      ['PLAN_MANAGER_ID', manager_id],
    ],
  });
}
function with_overrides(
  base: ConversationStartConfiguration,
  overrides: ConversationStartOverrides,
): ConversationStartConfiguration {
  if (overrides.backend_key != null && overrides.backend_key !== base.backend_key) {
    if (overrides.model == null)
      throw new PlannerError(
        'validation',
        "a start on a different backend has to name that backend's model",
        { backend_key: overrides.backend_key },
      );
    return {
      backend_key: overrides.backend_key,
      model: overrides.model,
      reasoning_effort: overrides.reasoning_effort ?? null,
    };
  }
  return {
    backend_key: base.backend_key,
    model: overrides.model ?? base.model,
    reasoning_effort:
      overrides.reasoning_effort == null && !overrides.reasoning_effort_is_set
        ? base.reasoning_effort
        : (overrides.reasoning_effort ?? null),
  };
}
export function resolve_manager_conversation_start(o: {
  manager_id: string;
  launch_configuration: ConversationStartConfiguration;
  overrides?: ConversationStartOverrides;
  workspace_folder: string;
  shared_workspace_folder: string;
}): ConversationStartValues {
  return {
    ...with_overrides(o.launch_configuration, o.overrides ?? NO_CONVERSATION_START_OVERRIDES),
    role_materials: manager_role_materials(o.manager_id, o.shared_workspace_folder),
    workspace_folder: o.workspace_folder,
    access: FLOOR_DEFAULT_ACCESS,
    workspace_path_policy: 'home_relative',
  };
}
export function resolve_worker_conversation_start(o: {
  ticket_id: string;
  worker_type_launch_defaults: ConversationStartConfiguration;
  ticket_last_chosen: ConversationStartConfiguration | null;
  overrides?: ConversationStartOverrides;
  workspace_folder: string;
  workspace_path_policy?: WorkspacePathPolicy;
}): ConversationStartValues {
  return {
    ...with_overrides(
      o.ticket_last_chosen ?? o.worker_type_launch_defaults,
      o.overrides ?? NO_CONVERSATION_START_OVERRIDES,
    ),
    role_materials: worker_conversation_role_materials(o.ticket_id),
    workspace_folder: o.workspace_folder,
    workspace_path_policy: o.workspace_path_policy ?? 'home_relative',
    access: FLOOR_DEFAULT_ACCESS,
  };
}
export function resolve_agent_conversation_start(o: {
  chief_launch_defaults: ConversationStartConfiguration;
  overrides?: ConversationStartOverrides;
  workspace_folder: string;
  shared_workspace_folder: string;
}): ConversationStartValues {
  return {
    ...with_overrides(o.chief_launch_defaults, o.overrides ?? NO_CONVERSATION_START_OVERRIDES),
    role_materials: chief_conversation_role_materials(o.shared_workspace_folder),
    workspace_folder: o.workspace_folder,
    access: FLOOR_DEFAULT_ACCESS,
    workspace_path_policy: 'home_relative',
  };
}
