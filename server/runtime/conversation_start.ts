/** Route-owned start/send/reset phases. No SQL capability survives an await. */
import { observe_conversation_operation, ConversationStartAborted } from './abort.ts';
import { randomUUID } from 'node:crypto';
import { mkdirSync, statSync, realpathSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';
import type { Database, ReadSql } from '../core/database/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import { pythonRepr } from '../core/config.ts';
import { PlannerError } from '../core/contracts.ts';
import {
  ConversationRoleMaterials,
  ConversationStartRequest,
  PromptDeliveryRefused,
  PromptDeliveryStarted,
  require_conversation_backend_key,
} from '../conversation/contracts.ts';
import type {
  ConversationSystem,
  ConversationMessageContent,
  NativeMessageOperation,
  PromptDeliveryFate,
  PromptDeliveryMode,
  WorkspacePathPolicy,
  SendOptions,
} from '../conversation/contracts.ts';
import type { MessageContent } from '../conversation/message_content.ts';
import { resolve_conversation_start_request } from '../conversation/logic/conversation_start_resolution.ts';
import { ensure_started_conversation_record } from '../conversation/record/data/store.ts';
import type { Ticket, TicketLocale } from '../tickets/contracts.ts';
import * as tickets from '../tickets/data/index.ts';
import * as locale from '../tickets/locale_configuration.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import type { WorkerTypeRegistry } from '../worker_types/registry.ts';
import { render_workflow_guide } from '../worker_types/guidance.ts';
import {
  CHIEF_SETTINGS_KEY,
  read_chief_settings,
  read_worker_launch_defaults_for_ticket_creation,
} from '../worker_settings/service.ts';
import type { Manager } from '../managers/contracts.ts';
import { manager_reuse_overrides } from '../managers/launch_settings.ts';
import {
  NO_CONVERSATION_START_OVERRIDES,
  resolve_worker_conversation_start,
  resolve_agent_conversation_start,
  resolve_manager_conversation_start,
} from './logic/conversation_start_resolution.ts';
import type {
  ConversationStartConfiguration,
  ConversationStartOverrides,
  ConversationStartValues,
} from './logic/conversation_start_resolution.ts';
export type {
  ConversationStartOverrides,
  ConversationStartValues,
} from './logic/conversation_start_resolution.ts';
export { NO_CONVERSATION_START_OVERRIDES } from './logic/conversation_start_resolution.ts';
export class ConversationLinkLock {
  private release(unlock: () => void): void {
    unlock();
  }
  private tail: Promise<void> = Promise.resolve();
  async run<T>(operation: () => Promise<T>, signal?: AbortSignal): Promise<T> {
    const previous = this.tail;
    let release!: () => void;
    const current = new Promise<void>((resolve) => {
      release = resolve;
    });
    this.tail = previous.then(() => current);
    try {
      await observe_conversation_operation(() => previous, signal);
      return await operation();
    } finally {
      this.release(release);
    }
  }
}
const locks = new Map<string, WeakRef<ConversationLinkLock>>();
export function conversation_link_lock(link_key: string): ConversationLinkLock {
  let lock = locks.get(link_key)?.deref();
  if (!lock) {
    lock = new ConversationLinkLock();
    locks.set(link_key, new WeakRef(lock));
  }
  return lock;
}
interface WorkspaceContext {
  db_path: string;
  workspace_root: string | null;
}
const contexts = new WeakMap<Database, WorkspaceContext>();
export function configure_conversation_start(database: Database, context: WorkspaceContext): void {
  contexts.set(database, context);
}
function context(database: Database, overrides: Partial<WorkspaceContext> = {}): WorkspaceContext {
  const registered = contexts.get(database);
  const db_path = overrides.db_path ?? registered?.db_path ?? process.env.PLAN_DB_PATH;
  if (!db_path) throw new Error('conversation workspace database is not registered');
  return {
    db_path: resolve(db_path),
    workspace_root:
      overrides.workspace_root ??
      registered?.workspace_root ??
      process.env.PLAN_WORKSPACE_ROOT ??
      null,
  };
}
export function default_host_workspace(): string {
  const preferred = join(homedir(), 'projects');
  try {
    if (statSync(preferred).isDirectory()) return realpathSync(preferred);
  } catch (error) {
    if (!(
      error instanceof Error &&
      'code' in error &&
      ['ENOENT', 'ENOTDIR'].includes(String(error.code))
    ))
      throw error;
  }
  return resolve(fileURLToPath(new URL('../../', import.meta.url)));
}
function default_workspace(database: Database, overrides: Partial<WorkspaceContext> = {}): string {
  return context(database, overrides).workspace_root ?? default_host_workspace();
}
export function chief_home_folder(database: Database): string {
  return join(dirname(context(database).db_path), 'files', 'chief');
}
export function manager_home_folder(database: Database, manager_id: string): string {
  return join(dirname(context(database).db_path), 'files', 'sprint-items', manager_id);
}
export function new_conversation_id(): string {
  return 'conv_' + randomUUID().replaceAll('-', '');
}
async function message_content_for(
  content: ConversationMessageContent,
  id: string,
): Promise<MessageContent> {
  return typeof content === 'function' ? await content(id) : content;
}
function require_enabled_model(sql: ReadSql, backend: string, model: string): void {
  const row = sql.get<{
    enabled: number;
  }>('SELECT enabled FROM backend_model_enablement WHERE backend_key=? AND model_id=?', [
    backend,
    model,
  ]);
  if (row && !row.enabled)
    throw new PlannerError('validation', `Model "${model}" is disabled. Choose an enabled model.`, {
      employee_backend: backend,
      employee_launch_model: model,
    });
}
export function mac_project_workspace_folder(project_id: string): string {
  return join('/', 'projects', project_id);
}
export function workspace_for_project(
  database: Database,
  ticket_locale: TicketLocale,
  project_id: string | null,
  options: {
    explicit?: string | null;
  } & Partial<WorkspaceContext> = {},
): [string, WorkspacePathPolicy] {
  if (options.explicit != null)
    return [options.explicit, ticket_locale === 'macos' ? 'absolute' : 'home_relative'];
  if (ticket_locale === 'macos')
    return [
      project_id === null ? '/' : mac_project_workspace_folder(project_id),
      'executor_default',
    ];
  if (project_id !== null) {
    const project = database.read((sql) =>
      sql.get<{
        folder_path: string | null;
      }>('SELECT folder_path FROM projects WHERE id=?', [project_id]),
    );
    if (!project) throw new PlannerError('validation', 'invalid project_id', { project_id });
    if (project.folder_path !== null) {
      try {
        if (statSync(project.folder_path).isDirectory())
          return [realpathSync(project.folder_path), 'home_relative'];
      } catch (error) {
        if (!(
          error instanceof Error &&
          'code' in error &&
          ['ENOENT', 'ENOTDIR'].includes(String(error.code))
        ))
          throw error;
      }
    }
  }
  return [default_workspace(database, options), 'home_relative'];
}
interface ResolveOptions extends Partial<WorkspaceContext> {
  worker_type_registry?: WorkerTypeRegistry;
  workspace_folder?: string | null;
}
export function worker_resolve(
  database: Database,
  ticket: Ticket,
  overrides: ConversationStartOverrides = NO_CONVERSATION_START_OVERRIDES,
  options: ResolveOptions = {},
): ConversationStartValues {
  const registry = options.worker_type_registry ?? configured_worker_type_registry();
  let defaults: ConversationStartConfiguration;
  if (ticket.employee_launch_model !== null) {
    defaults = {
      backend_key: require_conversation_backend_key(ticket.employee_backend),
      model: ticket.employee_launch_model,
      reasoning_effort: ticket.employee_launch_reasoning_effort,
    };
  } else {
    if (ticket.worker_type === null)
      throw new PlannerError('validation', 'custom Ticket requires a launch model');
    const launch = read_worker_launch_defaults_for_ticket_creation(registry, ticket.worker_type);
    defaults = {
      backend_key: require_conversation_backend_key(launch.employee_backend),
      model: launch.employee_launch_model,
      reasoning_effort: launch.employee_launch_reasoning_effort,
    };
  }
  const [workspace_folder, workspace_path_policy] = workspace_for_project(
    database,
    ticket.locale,
    ticket.project_id,
    { ...options, explicit: options.workspace_folder },
  );
  const values = resolve_worker_conversation_start({
    ticket_id: ticket.id,
    worker_type_launch_defaults: defaults,
    ticket_last_chosen: ticket.employee_launch_model === null ? null : defaults,
    overrides,
    workspace_folder,
    workspace_path_policy,
  });
  values.role_materials = new ConversationRoleMaterials({
    ...values.role_materials,
    role_text:
      values.role_materials.role_text +
      '\n\n[Ticket workflow guidance]\n' +
      render_workflow_guide(ticket.workflow) +
      '\n[/Ticket workflow guidance]',
  });
  if (ticket.employee_launch_model === null || overrides.model != null)
    database.read((sql) => require_enabled_model(sql, values.backend_key, values.model));
  return values;
}
export function agent_resolve_from_sql(
  sql: ReadSql,
  overrides: ConversationStartOverrides = NO_CONVERSATION_START_OVERRIDES,
  options: WorkspaceContext,
): ConversationStartValues {
  const launch = read_chief_settings(sql).launch_defaults;
  const values = resolve_agent_conversation_start({
    chief_launch_defaults: {
      backend_key: require_conversation_backend_key(launch.employee_backend),
      model: launch.employee_launch_model,
      reasoning_effort: launch.employee_launch_reasoning_effort,
    },
    overrides,
    workspace_folder: join(dirname(resolve(options.db_path)), 'files', 'chief'),
    shared_workspace_folder: options.workspace_root ?? default_host_workspace(),
  });
  require_enabled_model(sql, values.backend_key, values.model);
  return values;
}
export function agent_resolve(
  database: Database,
  overrides: ConversationStartOverrides = NO_CONVERSATION_START_OVERRIDES,
  options: ResolveOptions = {},
): ConversationStartValues {
  const values = database.read((sql) =>
    agent_resolve_from_sql(sql, overrides, context(database, options)),
  );
  if (options.workspace_folder != null) values.workspace_folder = options.workspace_folder;
  return values;
}
export function manager_resolve(
  database: Database,
  item: Manager,
  overrides: ConversationStartOverrides = NO_CONVERSATION_START_OVERRIDES,
  options: ResolveOptions = {},
): ConversationStartValues {
  const launch = item.manager_launch_configuration;
  return resolve_manager_conversation_start({
    manager_id: item.id,
    launch_configuration: {
      backend_key: require_conversation_backend_key(launch.employee_backend),
      model: launch.employee_launch_model,
      reasoning_effort: launch.employee_launch_reasoning_effort,
    },
    overrides,
    workspace_folder: options.workspace_folder ?? manager_home_folder(database, item.id),
    shared_workspace_folder: default_workspace(database, options),
  });
}
export interface LinkedConversation {
  conversation_id: string;
  made_here: boolean;
}
export interface DeliveredMessage {
  conversation_id: string | null;
  fate: PromptDeliveryFate;
}
function start_request(id: string, values: ConversationStartValues): ConversationStartRequest {
  return new ConversationStartRequest({ conversation_id: id, ...values });
}
export async function start_ticket_conversation(
  system: ConversationSystem,
  database: Database,
  ticket: Ticket,
  values: ConversationStartValues,
  options: {
    conversation_id: string;
    signal?: AbortSignal;
    now: number;
  },
): Promise<LinkedConversation> {
  const request = start_request(options.conversation_id, values);
  await observe_conversation_operation(() => system.start_conversation(request), options.signal);
  let linked: Ticket;
  try {
    // Python autocommits the durable identity before its separate guarded link.
    // If the link fails, the record survives and the runtime source is retired.
    database.write((tx) =>
      ensure_started_conversation_record(tx, resolve_conversation_start_request(request), {
        created_at: options.now,
      }),
    );
    const actual = database.read((sql) =>
      sql.get<{
        model: string | null;
        reasoning_effort: string | null;
      }>('SELECT model,reasoning_effort FROM conversations WHERE conversation_id=?', [
        request.conversation_id,
      ]),
    );
    linked = database.write((tx) =>
      tickets.write_ticket_conversation_start(tx, ticket.id, {
        conversation_id: request.conversation_id,
        backend: values.backend_key,
        model: actual ? actual.model : values.model,
        reasoning_effort: actual ? actual.reasoning_effort : values.reasoning_effort,
        now: options.now,
      }),
    );
  } catch (error) {
    try {
      await system.kill(options.conversation_id);
    } catch (cleanup) {
      if (error instanceof Error)
        Object.defineProperty(error, 'notes', {
          value: [
            `cleanup also failed for conversation ${options.conversation_id}: ${String(cleanup)}`,
          ],
        });
    }
    throw error;
  }
  const now_in = linked.conversation_id ?? options.conversation_id;
  return { conversation_id: now_in, made_here: now_in === options.conversation_id };
}
interface MessageOptions {
  signal?: AbortSignal;
  conversation_id: string | null;
  created_conversation_id?: string | null;
  runs_under?: ConversationStartOverrides;
  sender_label: string;
  mode?: PromptDeliveryMode;
  owner_read_through_sequence?: number | null;
  sender_message_id?: string | null;
  sent_at_unix_milliseconds?: number | null;
  sender?: Principal | null;
  recipient?: Principal | null;
  recording_receipt?: NativeMessageOperation | null;
}
export interface TicketMessageOptions extends MessageOptions {
  worker_type_registry?: WorkerTypeRegistry;
  retain_backend_start_failure?: boolean;
  now: number;
}
function send_options(
  options: MessageOptions,
  changes: boolean,
  record_refusal?: boolean,
): SendOptions {
  const runs = options.runs_under ?? NO_CONVERSATION_START_OVERRIDES;
  return {
    sender_label: options.sender_label,
    mode: options.mode ?? 'queue',
    owner_read_through_sequence: options.owner_read_through_sequence,
    sender_message_id: options.sender_message_id,
    sent_at_unix_milliseconds: options.sent_at_unix_milliseconds,
    sender: options.sender,
    recipient: options.recipient,
    recording_receipt: options.recording_receipt,
    ...(record_refusal === undefined ? {} : { record_refusal }),
    ...(changes
      ? {
          model_change: runs.model ?? null,
          reasoning_effort_change: runs.reasoning_effort ?? null,
          reasoning_effort_is_set: runs.reasoning_effort_is_set ?? false,
        }
      : {}),
  };
}
async function send_existing_ticket(
  system: ConversationSystem,
  database: Database,
  id: string,
  sending_into: string,
  content: MessageContent,
  options: TicketMessageOptions,
): Promise<DeliveredMessage> {
  const fate = await observe_conversation_operation(
    async () => system.send(sending_into, content, send_options(options, true)),
    options.signal,
  );
  const runs = options.runs_under ?? NO_CONVERSATION_START_OVERRIDES;
  if (
    fate instanceof PromptDeliveryStarted &&
    (runs.model != null || runs.reasoning_effort != null || runs.reasoning_effort_is_set)
  ) {
    database.write((tx) => {
      const current = tickets.read_ticket(tx, id);
      tickets.write_ticket_last_chosen_configuration(tx, id, {
        expected_conversation_id: sending_into,
        model: runs.model ?? current.employee_launch_model,
        reasoning_effort:
          runs.reasoning_effort == null && !runs.reasoning_effort_is_set
            ? current.employee_launch_reasoning_effort
            : (runs.reasoning_effort ?? null),
        now: options.now,
      });
    });
  }
  return { conversation_id: sending_into, fate };
}
async function let_go_ticket(
  system: ConversationSystem,
  database: Database,
  id: string,
  conversation_id: string,
  now: number,
): Promise<void> {
  await system.kill(conversation_id);
  database.write((tx) =>
    tickets.remove_ticket_conversation_start(tx, id, { conversation_id, now }),
  );
}
export async function send_to_ticket_conversation(
  system: ConversationSystem,
  database: Database,
  ticket_id: string,
  content: ConversationMessageContent,
  options: TicketMessageOptions,
): Promise<DeliveredMessage> {
  const ticket = database.read((sql) => tickets.read_ticket(sql, ticket_id));
  database.read((sql) => {
    if (!locale.can_execute_locale(sql, ticket.locale))
      throw new PlannerError(
        'validation',
        locale.execution_unavailable_reason(sql, ticket.locale),
        {
          ticket_id,
          locale: ticket.locale,
          machine_locale: locale.CURRENT_EXECUTION_LOCALE,
        },
      );
  });
  if (options.conversation_id === null && ticket.conversation_id === null) {
    const making = options.created_conversation_id ?? new_conversation_id();
    const values = worker_resolve(database, ticket, options.runs_under, {
      worker_type_registry: options.worker_type_registry,
    });
    const linked = await start_ticket_conversation(system, database, ticket, values, {
      conversation_id: making,
      now: options.now,
      signal: options.signal,
    });
    if (!linked.made_here) {
      await let_go_ticket(system, database, ticket_id, making, options.now);
      return send_existing_ticket(
        system,
        database,
        ticket_id,
        linked.conversation_id,
        await observe_conversation_operation(
          () => message_content_for(content, linked.conversation_id),
          options.signal,
        ),
        options,
      );
    }
    let fate: PromptDeliveryFate;
    try {
      fate = await observe_conversation_operation(
        async () =>
          system.send(
            making,
            await observe_conversation_operation(
              () => message_content_for(content, making),
              options.signal,
            ),
            send_options(options, true),
          ),
        options.signal,
      );
      if (fate instanceof PromptDeliveryStarted)
        database.write((tx) =>
          tickets.write_ticket_last_chosen_configuration(tx, ticket_id, {
            expected_conversation_id: making,
            employee_backend: values.backend_key,
            model: values.model,
            reasoning_effort: values.reasoning_effort,
            now: options.now,
          }),
        );
    } catch (error) {
      if (error instanceof ConversationStartAborted) throw error;
      await let_go_ticket(system, database, ticket_id, making, options.now);
      throw error;
    }
    if (fate instanceof PromptDeliveryRefused) {
      if (options.retain_backend_start_failure && fate.refusal_reason === 'backend_did_not_start')
        return { conversation_id: making, fate };
      await let_go_ticket(system, database, ticket_id, making, options.now);
      return { conversation_id: null, fate };
    }
    return { conversation_id: making, fate };
  }
  const sending_into = options.conversation_id ?? ticket.conversation_id;
  if (sending_into === null || sending_into !== ticket.conversation_id)
    throw new PlannerError('not_found', 'the ticket is not in that conversation', {
      ticket_id,
      conversation_id: options.conversation_id,
    });
  return send_existing_ticket(
    system,
    database,
    ticket_id,
    sending_into,
    await observe_conversation_operation(
      () => message_content_for(content, sending_into),
      options.signal,
    ),
    options,
  );
}
export function read_agent_conversation(database: Database, agent_key: string): string | null {
  return database.read(
    (sql) =>
      sql.get<{
        conversation_id: string | null;
      }>('SELECT conversation_id FROM agents WHERE agent_key=?', [agent_key])?.conversation_id ??
      null,
  );
}
/** Read the current links for the caller's roster. Missing links are omitted. */
export function read_agent_conversations(
  database: Database,
  agent_keys: Iterable<string>,
): Record<string, string> {
  const keys = [...new Set(agent_keys)];
  if (keys.length === 0) return {};
  return database.read((sql) =>
    Object.fromEntries(
      sql
        .all<{ agent_key: string; conversation_id: string }>(
          `SELECT agent_key, conversation_id
         FROM agents
         WHERE agent_key IN (${keys.map(() => '?').join(',')})
           AND conversation_id IS NOT NULL`,
          keys,
        )
        .map((row) => [row.agent_key, row.conversation_id]),
    ),
  );
}

/** Find durable transcripts by their original Manager identity, in creation order. */
export function read_manager_conversation_history(
  database: Database,
  manager_id: string,
): { conversation_id: string; created_at: number }[] {
  return database.read((sql) => {
    const rows = sql.all<{
      conversation_id: string;
      identity_environment_variables: string;
      created_at: number;
    }>(`SELECT conversation_id, identity_environment_variables, created_at
        FROM conversations
        ORDER BY created_at, conversation_id`);
    const history: { conversation_id: string; created_at: number }[] = [];
    for (const row of rows) {
      let identity: unknown;
      try {
        identity = JSON.parse(row.identity_environment_variables);
      } catch {
        continue;
      }
      if (!Array.isArray(identity)) continue;
      const values = new Map(
        identity
          .filter((pair): pair is [unknown, unknown] => Array.isArray(pair) && pair.length === 2)
          .map(
            ([key, value]) =>
              [
                typeof key === 'string' ? key : pythonRepr(key),
                typeof value === 'string' ? value : pythonRepr(value),
              ] as const,
          ),
      );
      if (values.get('PLAN_ACTOR') !== 'manager' || values.get('PLAN_MANAGER_ID') !== manager_id)
        continue;
      history.push({ conversation_id: row.conversation_id, created_at: row.created_at });
    }
    return history;
  });
}

export async function start_agent_conversation(
  system: ConversationSystem,
  database: Database,
  agent_key: string,
  values: ConversationStartValues,
  options: {
    conversation_id: string;
    signal?: AbortSignal;
    required_manager_id?: string | null;
  },
): Promise<LinkedConversation> {
  const home =
    agent_key === CHIEF_SETTINGS_KEY
      ? chief_home_folder(database)
      : options.required_manager_id != null
        ? manager_home_folder(database, options.required_manager_id)
        : null;
  if (home !== null && values.workspace_folder === home) mkdirSync(home, { recursive: true });
  await observe_conversation_operation(
    () => system.start_conversation(start_request(options.conversation_id, values)),
    options.signal,
  );
  const now_in = database.write((tx) => {
    if (options.required_manager_id == null)
      tx.run(
        'INSERT INTO agents(agent_key,conversation_id) VALUES (?,?) ON CONFLICT(agent_key) DO UPDATE SET conversation_id=excluded.conversation_id WHERE agents.conversation_id IS NULL',
        [agent_key, options.conversation_id],
      );
    else {
      const linked = tx.run(
        "UPDATE agents SET conversation_id=? WHERE agent_key=? AND conversation_id IS NULL AND EXISTS(SELECT 1 FROM managers WHERE id=? AND 'manager_' || id=agents.agent_key)",
        [options.conversation_id, agent_key, options.required_manager_id],
      );
      if (
        !linked.changes &&
        tx.get<{
          conversation_id: string | null;
        }>('SELECT conversation_id FROM agents WHERE agent_key=?', [agent_key])?.conversation_id ==
          null
      )
        return null;
    }
    return (
      tx.get<{
        conversation_id: string | null;
      }>('SELECT conversation_id FROM agents WHERE agent_key=?', [agent_key])?.conversation_id ??
      options.conversation_id
    );
  });
  if (now_in === null) {
    await system.kill(options.conversation_id);
    throw new PlannerError('not_found', 'Manager no longer exists', {
      manager_id: options.required_manager_id,
    });
  }
  return { conversation_id: now_in, made_here: now_in === options.conversation_id };
}
function unlink_agent(database: Database, agent_key: string, conversation_id: string): void {
  database.write((tx) =>
    tx.run('UPDATE agents SET conversation_id=NULL WHERE agent_key=? AND conversation_id=?', [
      agent_key,
      conversation_id,
    ]),
  );
}
async function let_go_agent(
  system: ConversationSystem,
  database: Database,
  agent_key: string,
  id: string,
): Promise<void> {
  await system.kill(id);
  unlink_agent(database, agent_key, id);
}
export interface AgentMessageOptions extends MessageOptions {
  required_manager_id?: string | null;
  retain_created_conversation?: boolean;
}
export async function send_to_agent_conversation(
  system: ConversationSystem,
  database: Database,
  agent_key: string,
  content: ConversationMessageContent,
  values: ConversationStartValues | null,
  options: AgentMessageOptions,
): Promise<DeliveredMessage> {
  let in_now = read_agent_conversation(database, agent_key);
  let runs_under = options.runs_under ?? NO_CONVERSATION_START_OVERRIDES;
  if (options.required_manager_id != null && in_now !== null) {
    const steering =
      options.mode === 'steer' &&
      (await observe_conversation_operation(() => system.is_running(in_now!), options.signal));
    runs_under = database.read((sql) =>
      manager_reuse_overrides(sql, options.required_manager_id!, values, runs_under, steering),
    );
  }
  const sending_options = { ...options, runs_under };
  if (options.conversation_id === null && in_now === null) {
    if (values === null)
      throw new PlannerError(
        'validation',
        'agent has no current conversation and no start configuration',
        { agent_key },
      );
    const making = options.created_conversation_id ?? new_conversation_id();
    const linked = await start_agent_conversation(system, database, agent_key, values, {
      conversation_id: making,
      required_manager_id: options.required_manager_id,
      signal: options.signal,
    });
    if (linked.made_here) {
      let fate: PromptDeliveryFate;
      try {
        fate = await observe_conversation_operation(
          async () =>
            system.send(
              making,
              await observe_conversation_operation(
                () => message_content_for(content, making),
                options.signal,
              ),
              send_options(sending_options, false, options.retain_created_conversation ?? false),
            ),
          options.signal,
        );
      } catch (error) {
        if (!options.retain_created_conversation)
          await let_go_agent(system, database, agent_key, making);
        throw error;
      }
      if (fate instanceof PromptDeliveryRefused && !options.retain_created_conversation) {
        await let_go_agent(system, database, agent_key, making);
        return { conversation_id: null, fate };
      }
      return { conversation_id: making, fate };
    }
    await let_go_agent(system, database, agent_key, making);
    in_now = linked.conversation_id;
  }
  const sending_into = options.conversation_id ?? in_now;
  if (sending_into === null || sending_into !== in_now)
    throw new PlannerError('not_found', 'the agent is not in that conversation', {
      agent_key,
      conversation_id: options.conversation_id,
    });
  const fate = await observe_conversation_operation(
    async () =>
      system.send(
        sending_into,
        await observe_conversation_operation(
          () => message_content_for(content, sending_into),
          options.signal,
        ),
        send_options(sending_options, true, options.retain_created_conversation ?? false),
      ),
    options.signal,
  );
  return { conversation_id: sending_into, fate };
}
export async function reset_agent_conversation(
  system: ConversationSystem,
  database: Database,
  agent_key: string,
): Promise<void> {
  await conversation_link_lock(`agent:${agent_key}`).run(async () => {
    const id = read_agent_conversation(database, agent_key);
    if (id === null) return;
    await system.kill(id);
    unlink_agent(database, agent_key, id);
  });
}
export async function reset_ticket_conversation(
  system: ConversationSystem,
  database: Database,
  ticket_id: string,
  options: {
    now: number;
  },
): Promise<void> {
  await conversation_link_lock(`ticket:${ticket_id}`).run(async () => {
    const id = database.read((sql) => tickets.read_ticket(sql, ticket_id)).conversation_id;
    if (id === null) return;
    await system.kill(id);
    database.write((tx) =>
      tickets.clear_ticket_conversation_link(tx, ticket_id, {
        expected_conversation_id: id,
        now: options.now,
      }),
    );
  });
}
