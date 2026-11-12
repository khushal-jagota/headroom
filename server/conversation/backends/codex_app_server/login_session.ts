/** One process-owned device-code sign-in. Evidence is drained before the final login read. */
import { randomBytes } from 'node:crypto';
import type { OwnedChildProcesses } from '../../../lifecycle/contracts.ts';
import { homedir } from 'node:os';
import {
  CodexSignInUnavailable,
  UnknownSignInSession,
  type CodexLoginGeneration,
  type CodexLoginRejectionMemory,
  type CodexSignIn,
  type CodexSignInService as SignInServiceContract,
  type SignInState,
  type StoredCodexLogin,
} from '../../snapshot/provider_contracts.ts';
import {
  CodexAppServerClient,
  type CodexClientStart,
  type CodexRequestOptions,
  type CodexServerMessageHandler,
} from './client.ts';
import { child_environment } from './environment.ts';
import { codex_home, read_stored_codex_login } from './login.ts';
import * as protocol from './protocol.ts';
import { DEFAULT_SIGN_IN_BOUNDS, type SignInBounds } from './sign_in_configuration.ts';
export { CodexSignInUnavailable, UnknownSignInSession } from '../../snapshot/provider_contracts.ts';
export type { SignInBounds } from './sign_in_configuration.ts';

export const START_METHOD = 'account/login/start';
export const CANCEL_METHOD = 'account/login/cancel';
export const COMPLETED_NOTIFICATION = 'account/login/completed';
export const SIGN_IN_NOTIFICATIONS = {
  [COMPLETED_NOTIFICATION]: protocol.AccountLoginCompletedNotification,
};

export interface SignInClient {
  readonly is_alive: boolean;
  start(options: CodexClientStart): Promise<void>;
  request(
    method: string,
    params?: Record<string, unknown>,
    options?: CodexRequestOptions,
  ): Promise<unknown>;
  notify(method: string, params?: Record<string, unknown>): Promise<void>;
  stop(): Promise<void>;
}
export interface CodexSignInOptions {
  child_processes: OwnedChildProcesses;
  resolve_argv(): Promise<readonly string[] | null>;
  remembered_login_rejection?: CodexLoginRejectionMemory | null;
  bounds?: Partial<SignInBounds>;
  now?: () => Date | string;
  read_login?: (home: string) => StoredCodexLogin;
  client_factory?: (options: ConstructorParameters<typeof CodexAppServerClient>[0]) => SignInClient;
}
type Delivered = { completion: protocol.AccountLoginCompletedNotification } | { child_ended: true };
interface Session {
  session_id: string;
  sequence: number;
  started_at: string;
  deadline_at: string;
  generation_at_start: CodexLoginGeneration | null;
  client: SignInClient;
  login_id: string | null;
  state: SignInState;
  detail: string | null;
  account_label: string | null;
  verification_url: string | null;
  user_code: string | null;
  delivered: Delivered[];
  completion: protocol.AccountLoginCompletedNotification | null;
  child_ended: boolean;
  reaped: boolean;
  reaper: ReturnType<typeof setTimeout> | null;
  consume_enabled: boolean;
  consume_queued: boolean;
  startup_aborted: boolean;
}

interface Timestamp {
  text: string;
  microseconds: bigint;
  offset_minutes: number;
}
function timestamp(value: Date | string): Timestamp {
  const text = value instanceof Date ? value.toISOString() : value;
  const match = /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(Z|[+-]\d\d:\d\d)$/.exec(text);
  if (match === null) throw new RangeError('Invalid sign-in timestamp');
  const offset = match[3] === 'Z' ? '+00:00' : match[3]!;
  const fraction = (match[2] ?? '').padEnd(6, '0');
  const milliseconds = Date.parse(match[1]! + offset);
  if (!Number.isFinite(milliseconds)) throw new RangeError('Invalid sign-in timestamp');
  const offset_minutes =
    (offset[0] === '-' ? -1 : 1) * (Number(offset.slice(1, 3)) * 60 + Number(offset.slice(4, 6)));
  return {
    text: `${match[1]}${fraction === '000000' ? '' : '.' + fraction}${offset}`,
    microseconds: BigInt(milliseconds) * 1000n + BigInt(fraction),
    offset_minutes,
  };
}
function deadline(start: Timestamp, seconds: number): string {
  const microseconds = start.microseconds + BigInt(Math.round(seconds * 1_000_000));
  const fraction = ((microseconds % 1_000_000n) + 1_000_000n) % 1_000_000n;
  const whole_seconds = (microseconds - fraction) / 1_000_000n;
  const local = new Date(Number(whole_seconds * 1000n) + start.offset_minutes * 60_000);
  const decimal = fraction === 0n ? '' : '.' + String(fraction).padStart(6, '0');
  return `${local.toISOString().slice(0, 19)}${decimal}${start.text.slice(-6)}`;
}
async function bounded<T>(operation: Promise<T>, seconds: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      operation,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => {
          const failure = new Error('');
          failure.name = 'TimeoutError';
          reject(failure);
        }, seconds * 1000);
      }),
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}
function same_generation(
  one: CodexLoginGeneration | null,
  other: CodexLoginGeneration | null,
): boolean {
  return one === null || other === null
    ? one === other
    : one.account_id === other.account_id && one.last_refresh === other.last_refresh;
}
function view(session: Session): CodexSignIn {
  return {
    session_id: session.session_id,
    state: session.state,
    verification_url: session.state === 'pending' ? session.verification_url : null,
    user_code: session.state === 'pending' ? session.user_code : null,
    started_at: session.started_at,
    deadline_at: session.deadline_at,
    detail: session.detail,
    account_label: session.account_label,
  };
}

export class CodexSignInService implements SignInServiceContract {
  private readonly options: CodexSignInOptions;
  private readonly bounds: SignInBounds;
  private readonly now: () => Date | string;
  private session: Session | null = null;
  private sequence = 0;
  private closed = false;
  private lock: Promise<void> = Promise.resolve();

  constructor(options: CodexSignInOptions) {
    this.options = options;
    this.bounds = { ...DEFAULT_SIGN_IN_BOUNDS, ...options.bounds };
    this.now = options.now ?? (() => new Date());
  }

  private with_lock<T>(operation: () => Promise<T>): Promise<T> {
    const task = this.lock.then(operation);
    this.lock = task.then(
      () => {},
      () => {},
    );
    return task;
  }

  read(): CodexSignIn | null {
    return this.session === null ? null : view(this.session);
  }

  start(): Promise<CodexSignIn> {
    return this.with_lock(async () => {
      if (this.closed) throw new CodexSignInUnavailable('Codex sign-in is shut down.');
      if (this.session?.state === 'pending') return view(this.session);
      return this.start_session();
    });
  }

  cancel(session_id: string): Promise<CodexSignIn> {
    return this.with_lock(async () => {
      const session = this.session;
      if (session === null || session.session_id !== session_id)
        throw new UnknownSignInSession(session_id);
      if (session.state !== 'pending') return view(session);
      await this.ask_to_cancel(session);
      return this.settle(session, false);
    });
  }

  shutdown(): Promise<void> {
    this.closed = true;
    return this.with_lock(async () => {
      const session = this.session;
      if (session === null) return;
      if (session.state !== 'pending') {
        this.stop_consumers(session);
        await this.stop_child(session);
      } else await this.settle(session, false);
    });
  }

  private read_login(): StoredCodexLogin {
    return (this.options.read_login ?? read_stored_codex_login)(codex_home());
  }

  private async start_session(): Promise<CodexSignIn> {
    this.sequence += 1;
    const started = timestamp(this.now());
    let session!: Session;
    const handler: CodexServerMessageHandler = {
      on_notification: async (method, notification) => {
        if (method === COMPLETED_NOTIFICATION)
          this.enqueue(session, {
            completion: notification as protocol.AccountLoginCompletedNotification,
          });
      },
      on_server_request: async () => {},
      on_child_ended: async () => {
        this.enqueue(session, { child_ended: true });
      },
    };
    const client = (
      this.options.client_factory ?? ((settings) => new CodexAppServerClient(settings))
    )({
      child_processes: this.options.child_processes,
      handler,
      description: `sign-in.${this.sequence}`,
      consumed_notifications: SIGN_IN_NOTIFICATIONS,
    });
    session = {
      session_id: randomBytes(12).toString('base64url'),
      sequence: this.sequence,
      started_at: started.text,
      deadline_at: deadline(started, this.bounds.session_seconds),
      generation_at_start: this.read_login().generation,
      client,
      login_id: null,
      state: 'pending',
      detail: null,
      account_label: null,
      verification_url: null,
      user_code: null,
      delivered: [],
      completion: null,
      child_ended: false,
      reaped: false,
      reaper: null,
      consume_enabled: false,
      consume_queued: false,
      startup_aborted: false,
    };
    this.session = session;
    let answer: unknown;
    try {
      await bounded(this.bring_up_child(session), this.bounds.child_startup_seconds);
      answer = await bounded(
        client.request(START_METHOD, { type: 'chatgptDeviceCode' }),
        this.bounds.child_startup_seconds,
      );
    } catch (failure) {
      session.startup_aborted = true;
      await this.stop_child(session);
      session.state = 'ended';
      session.detail = 'Panels could not start a sign-in with codex.';
      throw new CodexSignInUnavailable(
        failure instanceof Error ? failure.message : String(failure),
      );
    }
    try {
      const decoded = protocol.DeviceCodeLoginResponse.read(answer);
      session.login_id = decoded.loginId;
      session.verification_url = decoded.verificationUrl;
      session.user_code = decoded.userCode;
    } catch (failure) {
      await this.stop_child(session);
      session.state = 'ended';
      session.detail = 'Codex did not offer a device-code sign-in.';
      throw new CodexSignInUnavailable(
        `codex answered ${START_METHOD} with a shape this does not read: ${failure instanceof Error ? failure.message : String(failure)}`,
      );
    }
    session.reaper = setTimeout(() => {
      void this.with_lock(async () => {
        if (this.session !== session || session.state !== 'pending') return;
        session.reaped = true;
        await this.ask_to_cancel(session);
        await this.settle(session, false);
      }).catch(() => console.warn('codex sign-in: deadline settlement failed'));
    }, this.bounds.session_seconds * 1000);
    session.consume_enabled = true;
    if (session.delivered.length) this.schedule_consumer(session);
    return view(session);
  }

  private async bring_up_child(session: Session): Promise<void> {
    const argv = await this.options.resolve_argv();
    if (session.startup_aborted) return;
    if (argv === null) throw new CodexSignInUnavailable('codex is not installed on this machine');
    await session.client.start({
      argv,
      environment: child_environment(),
      working_directory: homedir(),
    });
    if (session.startup_aborted) return;
    await session.client.request('initialize', {
      clientInfo: { name: 'panels-sign-in', version: '1' },
    });
    if (session.startup_aborted) return;
    await session.client.notify('initialized');
  }

  private enqueue(session: Session, delivered: Delivered): void {
    // Never take the service lock from the reader that also delivers RPC responses.
    session.delivered.push(delivered);
    if (session.consume_enabled) this.schedule_consumer(session);
  }

  private schedule_consumer(session: Session): void {
    if (session.consume_queued) return;
    session.consume_queued = true;
    void this.with_lock(async () => {
      session.consume_queued = false;
      if (this.session !== session || !session.consume_enabled) return;
      this.absorb_delivered(session);
      if (session.state !== 'pending') {
        session.account_label = this.read_login().account_label;
        return;
      }
      if (session.completion !== null) await this.settle(session, session.completion.success);
      else if (session.child_ended) await this.settle(session, false);
    }).catch(() => console.warn('codex sign-in: inbox settlement failed'));
  }

  private absorb_delivered(session: Session): void {
    const delivered = session.delivered.splice(0);
    for (const said of delivered) {
      if ('child_ended' in said) {
        session.child_ended = true;
        continue;
      }
      const completion = said.completion;
      if (completion.loginId !== null && completion.loginId !== session.login_id) {
        console.warn(
          `codex sign-in: dropped a delivered ${COMPLETED_NOTIFICATION} for a login id this session does not own`,
        );
        continue;
      }
      if (session.completion?.success && !completion.success) continue;
      session.completion = completion;
    }
  }

  private async ask_to_cancel(session: Session): Promise<void> {
    if (session.login_id === null || !session.client.is_alive) return;
    try {
      await bounded(
        session.client.request(CANCEL_METHOD, { loginId: session.login_id }),
        this.bounds.cancel_seconds,
      );
    } catch {
      // A timeout or answer does not establish saved credential state.
    }
  }

  private stop_consumers(session: Session): void {
    if (session.reaper !== null) clearTimeout(session.reaper);
    session.reaper = null;
    session.consume_enabled = false;
  }

  private async stop_child(session: Session): Promise<void> {
    try {
      await session.client.stop();
    } catch {
      /* Python also suppresses stop errors. */
    }
  }

  private async settle(session: Session, success_was_reported: boolean): Promise<CodexSignIn> {
    this.stop_consumers(session);
    await this.stop_child(session);
    this.absorb_delivered(session);
    const reading = this.read_login();
    if (success_was_reported || session.completion?.success) {
      [session.state, session.detail] = this.judge_success(session, reading);
    } else {
      session.state = 'ended';
      session.detail = session.completion?.error
        ? `The sign-in ended: ${session.completion.error}`
        : session.reaped
          ? 'Panels stopped showing the code after its own time limit.'
          : session.child_ended
            ? 'The sign-in ended because codex stopped before it finished.'
            : 'The sign-in ended.';
    }
    session.account_label = reading.account_label;
    return view(session);
  }

  private judge_success(session: Session, reading: StoredCodexLogin): [SignInState, string] {
    if (!reading.present)
      return [
        'unconfirmed',
        'Codex reported a sign-in, but there is no saved login to confirm it.',
      ];
    if (!reading.readable)
      return [
        'unconfirmed',
        'Codex reported a sign-in, but Panels could not read the saved login.',
      ];
    if (reading.generation === null)
      return [
        'unconfirmed',
        'Codex reported a sign-in, but the saved login does not name which credential it holds.',
      ];
    if (same_generation(reading.generation, session.generation_at_start))
      return [
        'unconfirmed',
        'Codex reported a sign-in, but the saved login still holds the credential it had before.',
      ];
    if (this.options.remembered_login_rejection?.applies_to(reading.generation))
      return [
        'unconfirmed',
        'Codex reported a sign-in, but the credential now saved is one codex already refused.',
      ];
    if (reading.access_token_has_expired({ now: new Date(this.now()) }))
      return [
        'unconfirmed',
        'Codex reported a sign-in, but the credential now saved has already expired, so Panels cannot confirm it works.',
      ];
    return ['confirmed', 'Signed in.'];
  }
}
