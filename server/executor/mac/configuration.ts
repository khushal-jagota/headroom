/** Mac-owned runtime settings. Protocol types remain with the shared wire owner. */
import { isIP } from 'node:net';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { resolve_path_sync } from '../../core/path_resolution.ts';
import { strip } from '../../core/unidentified_agent_calls/logic.ts';
import { default_executor_root, executor_socket_path, expand_executor_path } from './platform.ts';
export { MAC_AWAY_AFTER_SECONDS } from '../configuration.ts';

export const MAX_ENVELOPE_BYTES = 1024 * 1024;
export const MAX_TOOL_DETAIL_CHARACTERS = 64 * 1024;
export const FILE_MODIFIED_WINDOW_NS = 2_000_000_000;
export const FILE_CHUNK_BYTES = 256 * 1024;
export const TRANSFER_CHUNK_BYTES = 64 * 1024;
export const CLI_RESULT_TIMEOUT_SECONDS = 30;
export const CLI_TRANSPORT_RESERVE_SECONDS = 5;
export const REQUEST_TIMEOUT_SECONDS = 30;
export const HTTP_CONNECT_TIMEOUT_SECONDS = 10;
export const HTTP_CLOSE_TIMEOUT_SECONDS = 5;
export const OUTBOX_RETRY_INITIAL_SECONDS = 1;
export const OUTBOX_RETRY_MAX_SECONDS = 60;
export const CONNECTION_FAILURE_LOG_INTERVAL_SECONDS = 60;
export const LAUNCH_AGENT_RESTART_THROTTLE_SECONDS = 10;
export const LEASE_INTERVAL_SECONDS = 10;
export const LEASE_TIMEOUT_SECONDS = 10;
export const LEASE_CONTINUITY_SECONDS = 25;
export const MAX_LEASE_WAITING_CONVERSATIONS = 64;
export const COMMAND_POLL_HOLD_SECONDS = 50;
export const COMMAND_POLL_TIMEOUT_SECONDS = 60;
export const MAX_MAILBOX_POLL_COMMANDS = 32;
export const MAX_MAILBOX_POLL_BYTES = 4 * 1024 * 1024;
export const MAX_COMMAND_OUTCOMES_PER_REPORT = 32;
export const MAX_COMMAND_OUTCOME_REPORT_BYTES = 4 * 1024 * 1024;
export const COMMAND_OUTCOME_TIMEOUT_SECONDS = 10;
export const STEER_DELIVERY_LIMIT_SECONDS = 10;
export const MODEL_LIST_LIMIT_SECONDS = 35;
export const MAX_ITEM_BATCH_ITEMS = 32;
export const MAX_ITEM_BATCH_BYTES = 4 * 1024 * 1024;
export const ITEM_BATCH_TIMEOUT_SECONDS = 30;
export const FILE_TRANSFER_STALL_SECONDS = 60;
export const INPUT_DOWNLOAD_RETRY_INITIAL_SECONDS = 1;
export const INPUT_DOWNLOAD_RETRY_MAX_SECONDS = 30;
export const LIVE_READ_TIMEOUT_SECONDS = 25;
export const MAX_LIVE_EVENTS_PER_BATCH = 64;
export const LIVE_EVENT_TIMEOUT_SECONDS = 5;
export const AGENT_CHILD_NICE = 10;
export const ORPHAN_AGENT_TERMINATION_GRACE_SECONDS = 10;

export interface MacHttpConnectorSettings {
  readonly limit: number;
  readonly force_close: boolean;
  readonly keepalive_timeout: number | null;
}
function connector(limit: number, force_close = false, keepalive_timeout: number | null = null) {
  return Object.freeze({ limit, force_close, keepalive_timeout });
}
export const MAC_LEASE_CONNECTOR = connector(1, true);
export const MAC_POLL_CONNECTOR = connector(1, true);
export const MAC_OUTCOME_CONNECTOR = connector(2, true);
export const MAC_ITEMS_CONNECTOR = connector(0, false, 15);
export const MAC_DOWNLOADS_CONNECTOR = connector(0, false, 15);
export const MAC_READS_CONNECTOR = connector(4, false, 15);
export const MAC_LIVE_CONNECTOR = connector(4, false, 5);

export const EXECUTOR_ROOT_ENV = 'PLAN_EXECUTOR_ROOT';
export const EXECUTOR_CHILD_ID_ENV = 'PLAN_EXECUTOR_CHILD_ID';
export const EXECUTOR_CAPABILITY_ENV = 'PLAN_EXECUTOR_CAPABILITY';
export const NATIVE_SKILLS_DIR_ENV = 'PLAN_NATIVE_SKILLS_DIR';
export const NATIVE_WORKER_CONTEXT =
  'For native execution, if PLAN_NATIVE_SKILLS_DIR exists, start with ' +
  'PLAN_NATIVE_SKILLS_DIR/panels/SKILL.md and read subsequent skill layers from ' +
  'that same root. If PLAN_TICKET_FILES_DIR exists, it supersedes all VPS-local ' +
  'Ticket artifact write paths in the skills. Write Ticket artifacts beneath ' +
  'PLAN_TICKET_FILES_DIR. Keep served /files/tickets/<ticket-id>/<relative-path> ' +
  'links in Ticket fields.';

export const MAC_UPDATE_SCHEMA_VERSION = 1;
export const MAC_RUNTIME_UPDATE_EXIT_CODE = 75;
export const MAC_RUNTIME_CHILD_ENV = 'PLAN_EXECUTOR_RUNTIME_CHILD';
export const MAC_RUNTIME_READY_ENV = 'PLAN_EXECUTOR_RUNTIME_READY';
export const MAC_RUNTIME_HANDOFF_ENV = 'PLAN_EXECUTOR_RUNTIME_HANDOFF';
export const MAC_UPDATE_POLL_SECONDS = 1;
export const MAC_RUNTIME_READY_TIMEOUT_SECONDS = 60;
export const MAC_UPDATE_TERMINATE_SECONDS = 10;
export const MAX_MAC_RUNTIME_ARCHIVE_BYTES = 2 * 1024 ** 3;
export const MAX_MAC_RUNTIME_EXPANDED_BYTES = 4 * 1024 ** 3;
export const MAX_MAC_UPDATE_RESPONSE_BYTES = 64 * 1024;
export const MAX_MAC_RUNTIME_MANIFEST_BYTES = 16 * 1024;
export const MAX_MAC_UPDATE_DETAIL_CHARACTERS = 500;
export const MAC_RUNTIME_ARCHIVE_NAME = 'panels-mac-runtime.tar.gz';
export const MAC_RUNTIME_MANIFEST_NAME = 'mac-runtime.json';

/** Persistent paths only. Construction opens neither a journal nor an outbox. */
export class ExecutorPaths {
  readonly root: string;
  constructor(root: string) {
    this.root = root;
  }
  get workspace(): string {
    return join(this.root, 'workspace');
  }
  get spool(): string {
    return join(this.root, 'spool');
  }
  get artifacts(): string {
    return join(this.root, 'artifacts', 'tickets');
  }
  get message_files_anchor(): string {
    return join(this.root, 'artifacts', 'message-files.db');
  }
  get skills(): string {
    return join(this.root, 'skills');
  }
  get run(): string {
    return join(this.root, 'run');
  }
  get ipc_socket(): string {
    return executor_socket_path(this.root);
  }
}
function value_error(message: string): never {
  throw Object.assign(new Error(message), { name: 'ValueError' });
}
export function executor_paths(environment: NodeJS.ProcessEnv = process.env): ExecutorPaths {
  const configured = strip(environment[EXECUTOR_ROOT_ENV] ?? '');
  const root = configured
    ? expand_executor_path(configured, environment)
    : default_executor_root(environment.HOME ?? homedir());
  return new ExecutorPaths(resolve_path_sync(root));
}

/** Preserve urlsplit's origin validation without WHATWG URL normalization. */
export function validate_server_url(value: string): string {
  if (/[\u0000-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/u.test(value))
    value_error('executor server URL contains whitespace or control characters');
  const parsed =
    /^(?:([A-Za-z][A-Za-z0-9+.-]*):)?(?:\/\/([^/?#]*))?([^?#]*)(?:\?([^#]*))?(?:#(.*))?$/u.exec(
      value,
    );
  const authority = parsed?.[2] ?? '';
  const host_port = authority.slice(authority.lastIndexOf('@') + 1);
  if (authority.includes('[') !== authority.includes(']')) value_error('Invalid IPv6 URL');
  let host: string;
  let port_text: string;
  if (authority.includes('[')) {
    if (!host_port.startsWith('[')) value_error('Invalid IPv6 URL');
    const end = host_port.indexOf(']');
    host = host_port.slice(1, end);
    const suffix = host_port.slice(end + 1);
    if (suffix && !suffix.startsWith(':')) value_error('Invalid IPv6 URL');
    if (host.startsWith('v')) {
      if (!/^v[0-9A-Fa-f]+\..+$/u.test(host)) value_error('IPvFuture address is invalid');
    } else {
      const scope = host.indexOf('%');
      const family = isIP(host.split('%', 1)[0]!);
      if (
        family === 0 ||
        (scope >= 0 &&
          (family !== 6 || !host.slice(scope + 1) || host.slice(scope + 1).includes('%')))
      )
        value_error(`'${host}' does not appear to be an IPv4 or IPv6 address`);
      if (family === 4) value_error('An IPv4 address cannot be in brackets');
    }
    port_text = suffix.slice(1);
  } else {
    const colon = host_port.indexOf(':');
    host = colon < 0 ? host_port : host_port.slice(0, colon);
    port_text = colon < 0 ? '' : host_port.slice(colon + 1);
  }
  const normalized = authority.replace(/[@:#?]/gu, '').normalize('NFKC');
  if (normalized !== authority.replace(/[@:#?]/gu, '') && /[/?#@:]/u.test(normalized))
    value_error(`netloc '${authority}' contains invalid characters under NFKC normalization`);
  if (port_text && (!/^[0-9]+$/u.test(port_text) || Number(port_text) > 65535))
    value_error('executor server URL has invalid port');
  if (
    !parsed ||
    (parsed[1] ?? '').toLowerCase() !== 'https' ||
    !host ||
    authority.includes('@') ||
    !['', '/'].includes(parsed[3]!) ||
    parsed[4] ||
    parsed[5] ||
    (port_text && Number(port_text) < 1)
  )
    value_error('executor server URL must be an HTTPS origin');
  return value.replace(/\/+$/u, '');
}
export function validate_executor_launch(
  executor_id: string,
  application_revision: string,
  server_url: string,
): void {
  if (!/^[A-Za-z0-9_.:-]{1,128}(?![\s\S])/u.test(executor_id) || !strip(application_revision))
    value_error('invalid explicit executor HTTPS launch configuration');
  validate_server_url(server_url);
}
