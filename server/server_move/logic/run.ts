/** The rules of one server move, free of files, HTTP and the database. */
import type { ServerLocation } from '../../server_link/contracts.ts';
import type {
  ServerMoveAttempt,
  ServerMoveHeldMarker,
  ServerMoveInstruction,
  ServerMovePhase,
  ServerMovePlatform,
  ServerMoveReport,
  ServerMoveRequest,
  ServerMoveView,
} from '../contracts.ts';
import { SERVER_MOVE_ACTIVE_PHASES, SERVER_MOVE_PAUSE_LIMIT_SECONDS } from '../contracts.ts';

/** The phases a failure can still end: everything before the point of no return. */
export const SERVER_MOVE_FAILABLE_PHASES: ReadonlySet<ServerMovePhase> = new Set([
  'preparing',
  'copying',
  'paused',
]);
/** The phases in which the old server holds its normal work. */
export const SERVER_MOVE_HOLDING_PHASES: ReadonlySet<ServerMovePhase> = new Set([
  'paused',
  'committing',
  'moved',
]);

export const RESTARTED_DURING_MOVE =
  'Panels restarted during the move, so the move stopped and nothing changed.';
export const PAUSE_TOO_LONG = `The pause got older than ${SERVER_MOVE_PAUSE_LIMIT_SECONDS} seconds before the switch.`;
export const MOVE_BLOCKS_UPDATE = 'A server move is running. Update after it ends.';

/** What the old server knows when the owner asks for a move. */
export interface ServerMoveStartFacts {
  readonly update_running: boolean;
  readonly move_running: boolean;
  readonly target_known: boolean;
  /** The target is the home identity or the machine marked as the server's own. */
  readonly target_runs_server: boolean;
  readonly target_present: boolean;
  /** The application revision of the target's last lease, or null without one. */
  readonly target_revision: string | null;
  readonly target_platform: string | null;
  /** The running server's released revision, or null for a source checkout. */
  readonly server_revision: string | null;
  readonly server_active: boolean;
}

export function start_refusal(facts: ServerMoveStartFacts): string | null {
  if (facts.update_running) return 'An update is running. Move the server after it ends.';
  if (facts.move_running) return 'Another server move is running.';
  if (!facts.server_active) return 'This server has already moved.';
  if (!facts.target_known) return 'This machine is unknown.';
  if (facts.target_runs_server) return 'The server already runs on this machine.';
  if (facts.server_revision === null || !/^[0-9a-f]{40}$/.test(facts.server_revision))
    return 'This server has no released revision, so it has no package to move.';
  if (facts.target_platform !== 'linux' && facts.target_platform !== 'darwin')
    return 'This machine has no server package for its platform.';
  if (!facts.target_present) return 'This machine is away. Move the server when it is back.';
  if (facts.target_revision !== facts.server_revision)
    return 'The machine code on this machine is out of date or cannot move the server. Update it first.';
  return null;
}

/**
 * The owner's request, checked. `validate_address` is the server link's own address rule; the
 * move also requires HTTPS, because the other machines reach the new server over it.
 */
export function parse_move_request(
  body: unknown,
  validate_address: (value: string) => string,
): ServerMoveRequest {
  if (body === null || typeof body !== 'object' || Array.isArray(body))
    throw new Error('A server move needs a server_url and a port.');
  const value = body as Record<string, unknown>;
  if (Object.keys(value).some((key) => key !== 'server_url' && key !== 'port'))
    throw new Error('A server move takes only a server_url and a port.');
  if (typeof value.server_url !== 'string')
    throw new Error('The new server address must be an HTTPS origin.');
  let server_url: string;
  try {
    server_url = validate_address(value.server_url);
  } catch {
    throw new Error('The new server address must be an HTTPS origin.');
  }
  if (!server_url.startsWith('https:'))
    throw new Error('The new server address must be an HTTPS origin.');
  const port = value.port;
  if (typeof port !== 'number' || !Number.isInteger(port) || port < 1 || port > 65535)
    throw new Error('The port must be a whole number from 1 to 65535.');
  return { server_url, port };
}

export function parse_move_report(body: unknown): ServerMoveReport {
  if (body !== null && typeof body === 'object' && !Array.isArray(body)) {
    const value = body as Record<string, unknown>;
    if (value.state === 'checked') return { state: 'checked' };
    if (value.state === 'failed' && typeof value.reason === 'string' && value.reason.trim())
      return { state: 'failed', reason: value.reason.trim().slice(0, 2000) };
  }
  throw new Error("A move report is { state: 'checked' } or { state: 'failed', reason }.");
}

export function new_attempt(input: {
  readonly move_id: string;
  readonly target_machine_id: string;
  readonly request: ServerMoveRequest;
  readonly platform: ServerMovePlatform;
  readonly revision: string;
  readonly location: ServerLocation;
  readonly trusted_ingress: ServerMoveAttempt['trusted_ingress'];
  readonly now_ms: number;
}): ServerMoveAttempt {
  return {
    move_id: input.move_id,
    target_machine_id: input.target_machine_id,
    server_url: input.request.server_url,
    port: input.request.port,
    phase: 'preparing',
    failure: null,
    started_at_ms: input.now_ms,
    updated_at_ms: input.now_ms,
    platform: input.platform,
    revision: input.revision,
    server_id: input.location.server_id,
    move_number: input.location.move_number,
    package: null,
    trusted_ingress: input.trusted_ingress,
    paused_at_ms: null,
  };
}

/** The phase order. A failure can come only before committing. */
const NEXT: Readonly<Record<ServerMovePhase, readonly ServerMovePhase[]>> = {
  preparing: ['copying', 'failed'],
  copying: ['paused', 'failed'],
  paused: ['committing', 'failed'],
  committing: ['moved'],
  moved: [],
  failed: [],
};

export function transition(
  attempt: ServerMoveAttempt,
  phase: ServerMovePhase,
  now_ms: number,
  changes: Partial<Pick<ServerMoveAttempt, 'package' | 'paused_at_ms' | 'failure'>> = {},
): ServerMoveAttempt {
  if (!NEXT[attempt.phase].includes(phase))
    throw new Error(`A server move cannot go from ${attempt.phase} to ${phase}.`);
  return { ...attempt, ...changes, phase, updated_at_ms: now_ms };
}

export function view_of(attempt: ServerMoveAttempt): ServerMoveView {
  return {
    move_id: attempt.move_id,
    target_machine_id: attempt.target_machine_id,
    server_url: attempt.server_url,
    port: attempt.port,
    phase: attempt.phase,
    failure: attempt.failure,
    started_at_ms: attempt.started_at_ms,
    updated_at_ms: attempt.updated_at_ms,
  };
}

export function instruction_of(attempt: ServerMoveAttempt): ServerMoveInstruction {
  return {
    move_id: attempt.move_id,
    phase: attempt.phase,
    server_url: attempt.server_url,
    port: attempt.port,
    server_id: attempt.server_id,
    move_number: attempt.move_number,
    package: attempt.package,
    trusted_ingress: attempt.trusted_ingress,
  };
}

/** A target learns of the run from its lease while the run is active. */
export function notice_applies(attempt: ServerMoveAttempt | null, machine_id: string): boolean {
  return (
    attempt !== null &&
    attempt.target_machine_id === machine_id &&
    SERVER_MOVE_ACTIVE_PHASES.has(attempt.phase)
  );
}

/** The instruction also tells a target that its run failed, so it cleans up. */
export function instruction_applies(
  attempt: ServerMoveAttempt | null,
  machine_id: string,
): boolean {
  return attempt !== null && attempt.target_machine_id === machine_id && attempt.phase !== 'moved';
}

export function pause_expired(attempt: ServerMoveAttempt, now_ms: number): boolean {
  return (
    attempt.phase === 'paused' &&
    attempt.paused_at_ms !== null &&
    now_ms - attempt.paused_at_ms >= SERVER_MOVE_PAUSE_LIMIT_SECONDS * 1000
  );
}

/**
 * The decision on a 'checked' report: commit only when the new address answers for this
 * server at the same move number, still active, and the pause is within its limit.
 */
export function report_decision(
  attempt: ServerMoveAttempt,
  answer: ServerLocation | null,
  now_ms: number,
): { readonly commit: true } | { readonly commit: false; readonly reason: string } {
  if (attempt.phase !== 'paused')
    return { commit: false, reason: 'The move was not paused when the check arrived.' };
  if (pause_expired(attempt, now_ms)) return { commit: false, reason: PAUSE_TOO_LONG };
  if (answer === null)
    return { commit: false, reason: `The new address ${attempt.server_url} did not answer.` };
  if (
    answer.server_id !== attempt.server_id ||
    answer.move_number !== attempt.move_number ||
    answer.state !== 'active'
  )
    return {
      commit: false,
      reason: `The new address ${attempt.server_url} does not answer for this server.`,
    };
  return { commit: true };
}

/** In committing, the new server has taken over once its address shows the next number. */
export function new_server_took_over(
  attempt: ServerMoveAttempt,
  answer: ServerLocation | null,
): boolean {
  return (
    answer !== null &&
    answer.server_id === attempt.server_id &&
    answer.move_number === attempt.move_number + 1 &&
    answer.state === 'active'
  );
}

/** What a restart of the old server does with the run it finds. */
export function startup_recovery(attempt: ServerMoveAttempt | null): 'fail' | 'probe' | 'none' {
  if (attempt === null) return 'none';
  if (SERVER_MOVE_FAILABLE_PHASES.has(attempt.phase)) return 'fail';
  return attempt.phase === 'committing' ? 'probe' : 'none';
}

/**
 * The held server's commit. It answers 'commit' for the copied record at number N, 'done'
 * when an interrupted commit already wrote N + 1, and refuses anything else.
 */
export function held_commit_state(
  marker: ServerMoveHeldMarker,
  location: ServerLocation | null,
): 'commit' | 'done' {
  if (location === null || location.server_id !== marker.server_id)
    throw new Error('The copied record belongs to another server.');
  if (location.state !== 'active') throw new Error('The copied record is not active.');
  if (location.move_number === marker.move_number) return 'commit';
  if (location.move_number === marker.move_number + 1 && location.server_url === marker.server_url)
    return 'done';
  throw new Error('The copied record has another move number.');
}
