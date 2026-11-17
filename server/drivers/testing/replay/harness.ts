/**
 * Run one scenario through a driver's real adapter, SDK or client, and wire parser, with a
 * replay process in place of the vendor executable.
 *
 * Supervision uses the real process primitives with containment off, under a records root in
 * the run's own folder, so a stop proves cleanup by process absence on any host.
 */
import { readFileSync } from 'node:fs';
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as B from '../../../conversation/backends/contracts.ts';
import type {
  BackendChild,
  BackendChildFactory,
  BackendEventValue,
  TurnToken,
} from '../../../conversation/backends/contracts.ts';
import {
  ConversationAccess,
  PromptDeliveryMode,
  ResolvedConversationStart,
  WorkspacePathPolicy,
} from '../../../conversation/contracts.ts';
import { text_message_content } from '../../../conversation/message_content.ts';
import type { ConversationMessageFiles } from '../../../conversation/message_files.ts';
import { monotonic_seconds } from '../../../lifecycle/configuration.ts';
import { ChildProcesses, OneShotChildren } from '../../../lifecycle/owned_processes.ts';
import { DarwinWorkerProcesses } from '../../../platform_runtime/darwin/worker_processes.ts';
import { LinuxWorkerProcesses } from '../../../platform_runtime/linux/worker_processes.ts';
import { worker_process_root_environment } from '../../../worker_supervision/config.ts';
import { WorkerSupervisionRuntime } from '../../../worker_supervision/runtime.ts';
import { create_claude_backend_child_factory } from '../../claude/runtime/adapter.ts';
import {
  codex_app_server_child_launch,
  create_codex_backend_child_factory,
} from '../../codex/runtime/adapter.ts';
import { RememberedCodexLoginRejection } from '../../codex/runtime/login.ts';
import { create_hermes_backend_child_factory } from '../../hermes/runtime/adapter.ts';
import { hermes_acp_child_launch } from '../../hermes/runtime/launch.ts';
import { parse_backend_instance_id } from '../../core/identity.ts';
import type { PeerStatus } from './peer.ts';
import type { ReplayScenario } from './scenarios.ts';
import { read_transcript } from './transcript.ts';
import type { Transcript } from './transcript.ts';

/** Without containment the parent proves cleanup by process absence, so CI needs no cgroup. */
class UncontainedLinuxWorkerProcesses extends LinuxWorkerProcesses {
  // The base class types its flag as the literal `true`. Supervision reads it as a boolean.
  override readonly requires_containment = false as true;
}

export interface ReplayEvent {
  readonly turn: TurnToken | null;
  readonly event: BackendEventValue;
}
export interface ReplayRun {
  readonly transcript: Transcript;
  readonly events: readonly ReplayEvent[];
  readonly accepted_turns: readonly TurnToken[];
  readonly refused_turns: readonly TurnToken[];
  readonly peer: PeerStatus;
}
export interface DriverEvidence {
  readonly events: readonly ReplayEvent[];
  readonly accepted_turns: readonly TurnToken[];
  readonly refused_turns: readonly TurnToken[];
}

const CONVERSATION_ID = 'replay';
/** A dead address: nothing in a replay may reach a Panels server. */
const PANELS_SERVER_URL = 'http://127.0.0.1:9';
const EVENT_TIMEOUT_MS = 15_000;
const STOP_SECONDS = 10;
const PEER = new URL('./peer.ts', import.meta.url);

/** The folders one run owns, all under one temporary directory. */
export interface RunFolders {
  readonly root: string;
  readonly workspace: string;
  readonly records: string;
  readonly home: string;
}
export async function run_folders(): Promise<RunFolders> {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'replay-')));
  const folders = {
    root,
    workspace: join(root, 'workspace'),
    records: join(root, 'records'),
    home: join(root, 'home'),
  };
  for (const folder of [folders.workspace, folders.records, folders.home])
    await mkdir(folder, { recursive: true });
  return folders;
}
/** A `#!node` file that sets its environment and imports a module, as native_rehearsal does. */
export async function write_shim(
  path: string,
  module: URL,
  environment: Readonly<Record<string, string>>,
): Promise<void> {
  const text = `#!${process.execPath}\nObject.assign(process.env, ${JSON.stringify(environment)});\nawait import(${JSON.stringify(module.href)});\n`;
  await writeFile(path, text, { mode: 0o755 });
}

/** The native owners a production server shares between its drivers and its core. */
export interface ReplayServices {
  readonly child_processes: ChildProcesses;
  readonly one_shot_children: OneShotChildren;
  readonly worker_supervision: WorkerSupervisionRuntime;
}
export function replay_services(): ReplayServices {
  const child_processes = new ChildProcesses();
  const one_shot_children = new OneShotChildren(child_processes);
  const primitives =
    process.platform === 'darwin'
      ? new DarwinWorkerProcesses({ children: one_shot_children, clock: monotonic_seconds })
      : new UncontainedLinuxWorkerProcesses({ clock: monotonic_seconds });
  return {
    child_processes,
    one_shot_children,
    worker_supervision: new WorkerSupervisionRuntime({ primitives, clock: monotonic_seconds }),
  };
}
/** The real factory of each built-in driver, launching `executable` in place of the vendor's. */
export function driver_factory(
  driver_kind: string,
  executable: string,
  folders: RunFolders,
  owned: ReplayServices,
): BackendChildFactory {
  const records = worker_process_root_environment(folders.records);
  if (driver_kind === 'claude')
    return create_claude_backend_child_factory(
      { claude_executable: executable, environment_overrides: [records] },
      owned,
    );
  if (driver_kind === 'codex') {
    const launch = codex_app_server_child_launch({
      codex_executable: executable,
      panels_server_url: PANELS_SERVER_URL,
    });
    return create_codex_backend_child_factory(
      { ...launch, environment_overrides: [...(launch.environment_overrides ?? []), records] },
      {
        child_processes: owned.child_processes,
        worker_supervision: owned.worker_supervision,
        remembered_login_rejection: new RememberedCodexLoginRejection(),
      },
    );
  }
  if (driver_kind === 'hermes') {
    const launch = hermes_acp_child_launch({
      hermes_python: executable,
      hermes_home: join(folders.home, '.hermes'),
      hermes_python_source_root: folders.root,
      panels_server_url: PANELS_SERVER_URL,
    });
    return create_hermes_backend_child_factory(
      { ...launch, environment_overrides: [...launch.environment_overrides, records] },
      { child_processes: owned.child_processes, worker_supervision: owned.worker_supervision },
    );
  }
  throw new Error(`no replay launch for driver ${driver_kind}`);
}
const NO_MESSAGE_FILES: ConversationMessageFiles = {
  keep: () => Promise.reject(new Error('replay prompts carry no files')),
  read: () => Promise.reject(new Error('replay prompts carry no files')),
  media_type_of: async () => null,
  path_of: () => {
    throw new Error('replay prompts carry no files');
  },
};

class EventLog {
  readonly events: ReplayEvent[] = [];
  readonly #waiters = new Set<() => void>();
  readonly #taken = new Map<string, number>();
  report = async (turn: TurnToken | null, event: BackendEventValue): Promise<void> => {
    this.events.push({ turn, event });
    for (const waiter of this.#waiters) waiter();
  };
  /** The next event of this class that no earlier wait took. */
  async next(event_class: string, describe: () => string): Promise<ReplayEvent> {
    const wanted = (this.#taken.get(event_class) ?? 0) + 1;
    const found = () => this.events.filter(({ event }) => event.constructor.name === event_class);
    if (found().length < wanted) {
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => {
          this.#waiters.delete(check);
          reject(new Error(`no ${event_class} within ${EVENT_TIMEOUT_MS} ms; ${describe()}`));
        }, EVENT_TIMEOUT_MS);
        const check = () => {
          if (found().length < wanted) return;
          clearTimeout(timer);
          this.#waiters.delete(check);
          resolve();
        };
        this.#waiters.add(check);
      });
    }
    this.#taken.set(event_class, wanted);
    return found()[wanted - 1]!;
  }
}
export function event_class_names(events: readonly ReplayEvent[]): string[] {
  return events.map(({ event }) => event.constructor.name);
}

/** Drive one child through the scenario's steps. The child launches `executable`. */
export async function drive_driver(options: {
  readonly driver_kind: string;
  readonly model: string;
  readonly executable: string;
  readonly folders: RunFolders;
  readonly scenario: ReplayScenario;
  /** Called on a failed step, so the error can name what the peer saw. */
  readonly describe?: () => string;
}): Promise<DriverEvidence> {
  const owned = replay_services();
  const log = new EventLog();
  const accepted_turns: TurnToken[] = [];
  const refused_turns: TurnToken[] = [];
  const resolved_start = new ResolvedConversationStart({
    conversation_id: CONVERSATION_ID,
    backend_key: parse_backend_instance_id(options.driver_kind),
    model: options.model,
    reasoning_effort: null,
    role_materials: null,
    workspace_folder: options.folders.workspace,
    access: ConversationAccess.full,
    workspace_path_policy: WorkspacePathPolicy.absolute,
  });
  const child: BackendChild = driver_factory(
    options.driver_kind,
    options.executable,
    options.folders,
    owned,
  )({ resolved_start, event_sink: { report: log.report }, message_files: NO_MESSAGE_FILES });
  const describe = () =>
    `events ${JSON.stringify(event_class_names(log.events))}; ${options.describe?.() ?? ''}`;
  let turn_number = 0;
  let stopped = false;
  const deadline = () => ({ deadline: monotonic_seconds() + STOP_SECONDS });
  const current_turn = () => {
    const turn = accepted_turns.at(-1);
    if (turn === undefined) throw new Error('this step needs an accepted turn');
    return turn;
  };
  try {
    await child.confirm_cli_compatible?.();
    await child.start(resolved_start, { vendor_session_cursor: null });
    for (const step of options.scenario.steps) {
      if (step.step === 'write_prompt') {
        const turn = { conversation_id: CONVERSATION_ID, turn_number: (turn_number += 1) };
        const content = text_message_content(step.text);
        try {
          await child.write_prompt(turn, content, {
            sender_label: 'Owner',
            sender_content: content,
            mode: step.mode ?? PromptDeliveryMode.queue,
            model_change: null,
            reasoning_effort_change: null,
          });
          accepted_turns.push(turn);
        } catch (error) {
          refused_turns.push(turn);
          throw new Error(`write_prompt was refused: ${String(error)}; ${describe()}`, {
            cause: error,
          });
        }
      } else if (step.step === 'steer') {
        const content = text_message_content(step.text);
        const outcome = await child.steer(current_turn(), content, {
          sender_label: 'Owner',
          sender_content: content,
        });
        if (!(outcome instanceof B.BackendSteerAccepted))
          throw new Error(`the steer was not accepted: ${JSON.stringify(outcome)}; ${describe()}`);
      } else if (step.step === 'cancel_running_turn') {
        await child.cancel_running_turn();
      } else if (step.step === 'answer_permission_ask') {
        const raised = log.events.filter(({ event }) => event instanceof B.PermissionAskRaised);
        const ask = (raised.at(-1)?.event as B.PermissionAskRaised | undefined)?.ask;
        const option = ask?.options.find((each) => each.option_kind === step.option_kind);
        if (ask === undefined || option === undefined)
          throw new Error(`no raised ask offers ${step.option_kind}; ${describe()}`);
        const fate = await child.answer_permission_ask(ask.ask_id, option.option_id, {
          turn_token: current_turn(),
        });
        if (fate !== 'applied') throw new Error(`the answer was ${fate}; ${describe()}`);
      } else if (step.step === 'await_event') {
        await log.next(step.event_class, describe);
      } else {
        stopped = true;
        await child.stop(deadline());
      }
    }
  } finally {
    if (!stopped) await child.stop(deadline()).catch(() => {});
    await owned.worker_supervision.close(deadline()).catch(() => {});
    await settle(owned.child_processes);
  }
  return { events: log.events, accepted_turns, refused_turns };
}
/** Every native child the run started has ended, or is killed. */
export async function settle(child_processes: ChildProcesses): Promise<void> {
  let timer: NodeJS.Timeout | undefined;
  const late = new Promise<'late'>((resolve) => {
    timer = setTimeout(() => resolve('late'), STOP_SECONDS * 1000);
  });
  const settled = child_processes.settled().then(() => 'settled' as const);
  try {
    if ((await Promise.race([settled, late])) === 'late') {
      child_processes.force_kill(monotonic_seconds() + 1);
      await settled;
      throw new Error('a replay child outlived its stop and was killed');
    }
  } finally {
    clearTimeout(timer);
  }
}

async function peer_status(path: string): Promise<PeerStatus | null> {
  try {
    return JSON.parse(await readFile(path, 'utf8')) as PeerStatus;
  } catch {
    return null;
  }
}
/** One transcript behind a peer shim, in a run folder of its own. */
export interface PreparedReplay {
  readonly transcript: Transcript;
  readonly folders: RunFolders;
  /** The shim the driver launches in place of the vendor executable. */
  readonly executable: string;
  /** The peer's status, for a failure message, while it may still run. */
  readonly status_now: () => string;
  /** The peer's final status. Throws unless every entry was reached and nothing failed. */
  readonly finished: () => Promise<PeerStatus>;
  /** The peer's failure, when it recorded one, as an error that wraps `cause`. */
  readonly explain: (cause: unknown) => Promise<unknown>;
}
export async function prepare_replay(
  path: string,
  scenario: ReplayScenario,
): Promise<PreparedReplay> {
  const transcript = read_transcript(path);
  if (transcript.scenario !== scenario.name)
    throw new Error(`${path} is a ${transcript.scenario} transcript, not ${scenario.name}`);
  const folders = await run_folders();
  const status_path = join(folders.root, 'peer-status.json');
  const executable = join(folders.root, transcript.driver_kind);
  await write_shim(executable, PEER, {
    REPLAY_TRANSCRIPT_PATH: path,
    REPLAY_STATUS_PATH: status_path,
    REPLAY_WORKSPACE: folders.workspace,
  });
  return {
    transcript,
    folders,
    executable,
    status_now: () => {
      try {
        return readFileSync(status_path, 'utf8');
      } catch {
        return 'not started';
      }
    },
    finished: async () => {
      const peer = await peer_status(status_path);
      if (peer === null) throw new Error(`the replay peer for ${path} never started`);
      if (peer.failure !== null) throw new Error(`replay peer failed: ${peer.failure}`);
      if (peer.cursor !== peer.total)
        throw new Error(`the replay stopped at entry ${peer.cursor + 1} of ${peer.total}`);
      return peer;
    },
    explain: async (cause) => {
      const failure = (await peer_status(status_path))?.failure;
      return failure ? new Error(`replay peer failed: ${failure}`, { cause }) : cause;
    },
  };
}
/**
 * Replay one transcript file. Every expected outbound frame must arrive, in order, and the
 * peer must report no failure. A step that fails names the peer's failure when it has one.
 */
export async function run_replay(path: string, scenario: ReplayScenario): Promise<ReplayRun> {
  const prepared = await prepare_replay(path, scenario);
  try {
    let evidence: DriverEvidence;
    try {
      evidence = await drive_driver({
        driver_kind: prepared.transcript.driver_kind,
        model: prepared.transcript.model,
        executable: prepared.executable,
        folders: prepared.folders,
        scenario,
        describe: () => `peer ${prepared.status_now()}`,
      });
    } catch (error) {
      throw await prepared.explain(error);
    }
    return { transcript: prepared.transcript, ...evidence, peer: await prepared.finished() };
  } finally {
    await rm(prepared.folders.root, { recursive: true, force: true });
  }
}
