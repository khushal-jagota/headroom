/**
 * Signing Panels' own agy in with a personal Google account from any device, and signing it out.
 *
 * agy 1.5.10 refuses ACP `authenticate` on a machine with no graphical display. Its terminal
 * sign-in, `agy acp --login`, prints a Google link whose return address is agy's hosted page
 * https://antigravity.google/oauth-callback, then reads that page's address from stdin. Panels
 * shows the link, takes the address the browser ended on, checks that it belongs to this sign-in
 * and hands it to agy. agy exchanges it, saves its token in its app data directory and exits.
 * A throwaway session then confirms the sign-in. The address carries a one-time code: Panels
 * never logs, stores or returns it.
 */
import { randomBytes } from 'node:crypto';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Readable, Writable } from 'node:stream';
import { StringDecoder } from 'node:string_decoder';
import { PROTOCOL_VERSION, ndJsonStream } from '@agentclientprotocol/sdk/unstable';
import type { InitializeResponse } from '@agentclientprotocol/sdk/unstable';
import { ensure_cli_compatible } from '../../../conversation/backends/cli_compatibility.ts';
import { observe_promise } from '../../../core/promise_observer.ts';
import type {
  OwnedChildProcesses,
  OwnedOneShotChildren,
  StopOptions,
} from '../../../lifecycle/contracts.ts';
import { spawn_owned_process } from '../../../lifecycle/owned_processes.ts';
import type { OwnedProcess } from '../../../lifecycle/owned_processes.ts';
import { AcpClient } from '../../acp/client.ts';
import { CHILD_OUTPUT_LINE_LIMIT_BYTES } from '../../acp/configuration.ts';
import { acp_child_environment } from '../../acp/environment.ts';
import type { AcpLaunch } from '../../acp/flavor.ts';
import {
  SignInReturnUrlRefused,
  SignInUnavailable,
  UnknownSignInSession,
} from '../../core/sign_in.ts';
import type { DriverSignInController, SignInSession, SignInState } from '../../core/sign_in.ts';
import { antigravity_driver } from '../driver.ts';
import { antigravity_acp_launch } from './flavor.ts';
import type { AntigravityLaunchOptions } from './flavor.ts';

export interface AntigravitySignInBounds {
  /** From launch until agy has printed its Google link. */
  readonly link_seconds: number;
  /** How long a started sign-in stays in force. */
  readonly session_seconds: number;
  /** From handing agy the return address until it has exchanged it and exited. */
  readonly exchange_seconds: number;
  /** The whole short-lived agy that signs out. */
  readonly sign_out_seconds: number;
}
export const ANTIGRAVITY_SIGN_IN_BOUNDS: AntigravitySignInBounds = {
  link_seconds: 30,
  session_seconds: 5 * 60,
  exchange_seconds: 60,
  sign_out_seconds: 60,
};
/** agy's hosted return page. Google sends the browser there with the code and the state. */
export const ANTIGRAVITY_RETURN_ADDRESS = 'https://antigravity.google/oauth-callback';
const GOOGLE_ORIGIN = 'https://accounts.google.com';
const GOOGLE_AUTHORIZATION_PATHS = ['/o/oauth2/auth', '/o/oauth2/v2/auth'];
const ADDRESS_LIMIT_CHARACTERS = 16_384;
const NOT_THIS_SIGN_IN = 'That address does not belong to this sign-in.';

export interface AntigravityAuthorization {
  readonly url: string;
  readonly state: string;
}
/** agy's Google link, or null when it is not a personal Google sign-in that returns to agy. */
export function read_authorization_url(text: string): AntigravityAuthorization | null {
  if (text.length > ADDRESS_LIMIT_CHARACTERS || /\s/.test(text) || !URL.canParse(text)) return null;
  const url = new URL(text);
  const only = (name: string) => {
    const values = url.searchParams.getAll(name);
    return values.length === 1 ? values[0]! : null;
  };
  const state = only('state');
  if (
    url.origin !== GOOGLE_ORIGIN ||
    !GOOGLE_AUTHORIZATION_PATHS.includes(url.pathname) ||
    url.username !== '' ||
    url.password !== '' ||
    url.hash !== '' ||
    only('response_type') !== 'code' ||
    only('redirect_uri') !== ANTIGRAVITY_RETURN_ADDRESS ||
    state === null ||
    state === '' ||
    state.length > 512 ||
    /\s/.test(state)
  )
    return null;
  return { url: text, state };
}

export type ReturnUrlReading =
  | { readonly kind: 'refused'; readonly reason: string }
  | { readonly kind: 'code' }
  | { readonly kind: 'error'; readonly error: string };
/** Whether a pasted address is Google's answer to the sign-in with this state. */
export function read_return_url(text: string, state: string): ReturnUrlReading {
  const refused = (reason: string) => ({ kind: 'refused', reason }) as const;
  // The URL parser drops tabs and newlines, which would reach agy's stdin as extra lines.
  if (text.length > ADDRESS_LIMIT_CHARACTERS || /\s/.test(text) || !URL.canParse(text))
    return refused('Paste the whole address the browser ended on after the Google sign-in.');
  const url = new URL(text);
  const expected = new URL(ANTIGRAVITY_RETURN_ADDRESS);
  if (
    url.origin !== expected.origin ||
    url.pathname !== expected.pathname ||
    url.username !== '' ||
    url.password !== '' ||
    url.hash !== ''
  )
    return refused(NOT_THIS_SIGN_IN);
  const states = url.searchParams.getAll('state');
  if (states.length !== 1 || states[0] !== state) return refused(NOT_THIS_SIGN_IN);
  const codes = url.searchParams.getAll('code');
  const errors = url.searchParams.getAll('error');
  const one_code = codes.length === 1 && codes[0] !== '' && errors.length === 0;
  const one_error = errors.length === 1 && errors[0] !== '' && codes.length === 0;
  if (!one_code && !one_error)
    return refused('That address does not carry one Google sign-in answer.');
  const issuers = url.searchParams.getAll('iss');
  if (issuers.length > 1 || (issuers.length === 1 && issuers[0] !== GOOGLE_ORIGIN))
    return refused('That address is not a Google sign-in answer.');
  return one_error ? { kind: 'error', error: errors[0]! } : { kind: 'code' };
}

/** One `agy acp --login`, in its own directory. */
interface AgyLogin {
  /** agy's Google link. Rejects when agy offers none it can be read from. */
  readonly link: Promise<AntigravityAuthorization>;
  /** agy's exit code once it has exited. */
  readonly exit_code: Promise<number | null>;
  hand(line: string): Promise<void>;
  /** Stops agy and removes its directory. May be called again. */
  stop(options?: StopOptions): Promise<void>;
}
async function start_agy_login(
  launch: AcpLaunch,
  child_processes: OwnedChildProcesses,
): Promise<AgyLogin> {
  const cwd = await mkdtemp(join(tmpdir(), 'panels-antigravity-sign-in-'));
  const held: { owned: OwnedProcess | null } = { owned: null };
  let stopping: Promise<void> | null = null;
  const stop = (options?: StopOptions) =>
    (stopping ??= (async () => {
      try {
        await held.owned?.stop(options);
      } finally {
        await rm(cwd, { recursive: true, force: true }).catch(() =>
          console.warn('Antigravity sign-in did not remove its directory'),
        );
      }
    })());
  let owned: OwnedProcess;
  try {
    owned = await spawn_owned_process([...launch.argv, '--login'], {
      child_processes,
      on_created: (created) => {
        held.owned = created;
      },
      environment: acp_child_environment(launch.environment_overrides, { role_materials: null }),
      working_directory: cwd,
    });
  } catch (error) {
    await stop();
    throw error;
  }
  // agy's log is not read, and must not fill its pipe.
  owned.child.stderr.resume();
  const link = new Promise<AntigravityAuthorization>((resolve, reject) => {
    const decoder = new StringDecoder('utf8');
    let pending = '';
    let settled = false;
    const settle = (outcome: AntigravityAuthorization | Error) => {
      settled = true;
      pending = '';
      if (outcome instanceof Error) reject(outcome);
      else resolve(outcome);
    };
    // The stream keeps flowing after the link, so agy never blocks on a full pipe.
    owned.child.stdout.on('data', (chunk: Buffer) => {
      if (settled) return;
      pending += decoder.write(chunk);
      let newline: number;
      while (!settled && (newline = pending.indexOf('\n')) !== -1) {
        const line = pending.slice(0, newline).trim();
        pending = pending.slice(newline + 1);
        if (line.startsWith(`${GOOGLE_ORIGIN}/`))
          settle(
            read_authorization_url(line) ??
              new Error('agy offered a Google link that is not a personal Google sign-in.'),
          );
      }
      if (!settled && pending.length > ADDRESS_LIMIT_CHARACTERS)
        settle(new Error('agy printed a line too long to be its sign-in link.'));
    });
    void owned.exited.then(() => {
      if (!settled) settle(new Error('agy stopped before it offered a Google link.'));
    });
  });
  void link.catch(() => {});
  return {
    link,
    exit_code: owned.exited.then(() => owned.child.exitCode),
    hand: (line) =>
      new Promise<void>((resolve, reject) =>
        owned.child.stdin.write(`${line}\n`, (error) => (error ? reject(error) : resolve())),
      ),
    stop,
  };
}

interface Session {
  readonly session_id: string;
  readonly started_at: Date;
  readonly deadline_at: Date;
  readonly executable: string;
  readonly login: AgyLogin;
  readonly authorization: AntigravityAuthorization;
  state: SignInState;
  detail: string | null;
  reaper: ReturnType<typeof setTimeout> | null;
}
function view(session: Session): SignInSession {
  return {
    session_id: session.session_id,
    state: session.state,
    verification_url: session.state === 'awaiting_return_url' ? session.authorization.url : null,
    user_code: null,
    started_at: session.started_at,
    deadline_at: session.deadline_at,
    detail: session.detail,
    account_label: null,
  };
}
function reason(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export interface AntigravitySignInDependencies {
  /** The agy the card and conversations run. */
  executable(): string;
  child_processes: OwnedChildProcesses;
  one_shot_children: OwnedOneShotChildren;
  /** Whether a throwaway session in this home starts signed in. */
  verify(executable: string): Promise<boolean>;
  now?: () => Date;
  bounds?: Partial<AntigravitySignInBounds>;
}
/** One sign-in in force at a time. Every change of state happens under one lock. */
export class AntigravitySignIn implements DriverSignInController {
  private readonly launch_options: Omit<AntigravityLaunchOptions, 'executable'>;
  private readonly dependencies: AntigravitySignInDependencies;
  private readonly bounds: AntigravitySignInBounds;
  private readonly shutting_down = new AbortController();
  private stop_options: StopOptions | undefined;
  private session: Session | null = null;
  private lock: Promise<void> = Promise.resolve();
  constructor(
    launch_options: Omit<AntigravityLaunchOptions, 'executable'>,
    dependencies: AntigravitySignInDependencies,
  ) {
    this.launch_options = launch_options;
    this.dependencies = dependencies;
    this.bounds = { ...ANTIGRAVITY_SIGN_IN_BOUNDS, ...dependencies.bounds };
  }
  read(): SignInSession | null {
    return this.session === null ? null : view(this.session);
  }
  start(): Promise<SignInSession> {
    return this.with_lock(async () => {
      if (this.shutting_down.signal.aborted)
        throw new SignInUnavailable('Antigravity sign-in is shut down.');
      if (this.session?.state === 'awaiting_return_url') return view(this.session);
      return this.start_session();
    });
  }
  cancel(session_id: string): Promise<SignInSession> {
    return this.with_lock(async () => {
      const session = this.in_force(session_id);
      if (session.state !== 'awaiting_return_url') return view(session);
      return this.settle(session, 'ended', 'The sign-in was cancelled.');
    });
  }
  submit_return_url(session_id: string, return_url: string): Promise<SignInSession> {
    return this.with_lock(async () => {
      const session = this.in_force(session_id);
      if (session.state !== 'awaiting_return_url') return view(session);
      const reading = read_return_url(return_url, session.authorization.state);
      // The session stays in force, so a mistyped paste can be corrected.
      if (reading.kind === 'refused') throw new SignInReturnUrlRefused(reading.reason);
      if (reading.kind === 'error')
        return this.settle(
          session,
          'ended',
          reading.error === 'access_denied'
            ? 'Google sign-in was not approved. Start sign-in again.'
            : 'Google did not complete the sign-in. Start sign-in again.',
        );
      try {
        await session.login.hand(return_url);
      } catch {
        return this.settle(session, 'ended', 'Panels could not hand the address to agy.');
      }
      const exit = await observe_promise(
        session.login.exit_code,
        AbortSignal.any([
          AbortSignal.timeout(this.bounds.exchange_seconds * 1000),
          this.shutting_down.signal,
        ]),
      );
      if (!exit.completed)
        return this.settle(session, 'ended', 'agy did not finish the sign-in in time.');
      if (exit.value !== 0)
        return this.settle(
          session,
          'ended',
          'Google did not accept that sign-in. Start sign-in again.',
        );
      await session.login.stop(this.stop_options);
      let signed_in: boolean;
      try {
        signed_in = await this.dependencies.verify(session.executable);
      } catch {
        return this.settle(
          session,
          'unconfirmed',
          'agy reported a sign-in, but Panels could not open a session to confirm it.',
        );
      }
      return signed_in
        ? this.settle(session, 'confirmed', 'Signed in.')
        : this.settle(
            session,
            'unconfirmed',
            'agy reported a sign-in, but a new session still asks to sign in.',
          );
    });
  }
  sign_out(): Promise<void> {
    return this.with_lock(async () => {
      if (this.session?.state === 'awaiting_return_url')
        await this.settle(this.session, 'ended', 'The sign-in was cancelled by sign-out.');
      await this.log_out();
    });
  }
  /** Ends the sign-in in force and stops its agy, within the shutdown deadline. */
  async shutdown(options?: StopOptions): Promise<void> {
    this.stop_options ??= options;
    if (!this.shutting_down.signal.aborted)
      this.shutting_down.abort(new Error('Antigravity sign-in is shut down'));
    await this.with_lock(async () => {
      if (this.session?.state === 'awaiting_return_url')
        await this.settle(this.session, 'ended', 'Panels stopped this sign-in when it shut down.');
    });
  }

  private with_lock<T>(operation: () => Promise<T>): Promise<T> {
    const task = this.lock.then(operation);
    this.lock = task.then(
      () => {},
      () => {},
    );
    return task;
  }
  private in_force(session_id: string): Session {
    if (this.session === null || this.session.session_id !== session_id)
      throw new UnknownSignInSession(session_id);
    return this.session;
  }
  private launch(executable: string): AcpLaunch {
    return antigravity_acp_launch({ ...this.launch_options, executable });
  }
  private async start_session(): Promise<SignInSession> {
    const executable = this.dependencies.executable();
    let login: AgyLogin;
    try {
      await ensure_cli_compatible(antigravity_driver.driver_kind, executable, {
        children: this.dependencies.one_shot_children,
        signal: this.shutting_down.signal,
      });
      login = await start_agy_login(this.launch(executable), this.dependencies.child_processes);
    } catch (error) {
      throw new SignInUnavailable(`Panels could not start agy to sign in: ${reason(error)}`);
    }
    const offered = await observe_promise(
      login.link,
      AbortSignal.any([
        AbortSignal.timeout(this.bounds.link_seconds * 1000),
        this.shutting_down.signal,
      ]),
    ).catch((error: unknown) => error as Error);
    if (offered instanceof Error || !offered.completed) {
      await login.stop(this.stop_options);
      throw new SignInUnavailable(
        offered instanceof Error ? offered.message : 'agy did not offer a Google link in time.',
      );
    }
    const started_at = (this.dependencies.now ?? (() => new Date()))();
    const session: Session = {
      session_id: randomBytes(12).toString('base64url'),
      started_at,
      deadline_at: new Date(started_at.getTime() + this.bounds.session_seconds * 1000),
      executable,
      login,
      authorization: offered.value,
      state: 'awaiting_return_url',
      detail: null,
      reaper: null,
    };
    this.session = session;
    session.reaper = setTimeout(
      () => this.end_if_waiting(session, 'Panels stopped waiting after its own time limit.'),
      this.bounds.session_seconds * 1000,
    );
    // An agy that stops by itself ends the sign-in. One stopped by Panels finds it settled.
    void login.exit_code.then(() =>
      this.end_if_waiting(session, 'agy stopped before the sign-in finished.'),
    );
    return view(session);
  }
  private end_if_waiting(session: Session, detail: string): void {
    void this.with_lock(async () => {
      if (this.session === session && session.state === 'awaiting_return_url')
        await this.settle(session, 'ended', detail);
    }).catch(() => console.warn('Antigravity sign-in: settlement failed'));
  }
  private async settle(
    session: Session,
    state: SignInState,
    detail: string,
  ): Promise<SignInSession> {
    if (session.reaper !== null) clearTimeout(session.reaper);
    session.reaper = null;
    session.state = state;
    session.detail = detail;
    await session.login.stop(this.stop_options);
    return view(session);
  }
  /** ACP `logout` through a short-lived `agy acp` with the conversations' launch. */
  private async log_out(): Promise<void> {
    const executable = this.dependencies.executable();
    const launch = this.launch(executable);
    const signal = AbortSignal.any([
      AbortSignal.timeout(this.bounds.sign_out_seconds * 1000),
      this.shutting_down.signal,
    ]);
    const cwd = await mkdtemp(join(tmpdir(), 'panels-antigravity-sign-out-'));
    const held: { owned: OwnedProcess | null } = { owned: null };
    let client: AcpClient | null = null;
    try {
      const owned = await spawn_owned_process(launch.argv, {
        child_processes: this.dependencies.child_processes,
        on_created: (created) => {
          held.owned = created;
        },
        environment: acp_child_environment(launch.environment_overrides, { role_materials: null }),
        working_directory: cwd,
      });
      owned.child.stderr.resume();
      client = new AcpClient(
        ndJsonStream(
          Writable.toWeb(owned.child.stdin),
          Readable.toWeb(owned.child.stdout) as ReadableStream<Uint8Array>,
          { maxMessageBytes: CHILD_OUTPUT_LINE_LIMIT_BYTES },
        ),
      );
      const initialized = await client.request<InitializeResponse>(
        'initialize',
        {
          protocolVersion: PROTOCOL_VERSION,
          clientInfo: { name: 'panels', title: 'Panels', version: '2.0.0' },
        },
        { signal },
      );
      if (!initialized.agentCapabilities?.auth?.logout)
        throw new Error('This agy cannot sign out through Panels.');
      await client.request('logout', {}, { signal });
    } finally {
      client?.close();
      try {
        await held.owned?.stop(this.stop_options);
      } finally {
        await rm(cwd, { recursive: true, force: true }).catch(() =>
          console.warn('Antigravity sign-out did not remove its directory'),
        );
      }
    }
  }
}
