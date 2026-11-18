/**
 * The card's model list comes from a throwaway `agy acp` session, started in a private directory
 * and stopped once it has answered. The session's files and the directory are removed after.
 * Opening the session also makes agy renew the CLI login when its access token has expired.
 */
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Readable, Writable } from 'node:stream';
import { PROTOCOL_VERSION, RequestError, ndJsonStream } from '@agentclientprotocol/sdk/unstable';
import type {
  NewSessionResponse,
  SessionConfigOption,
  SetSessionConfigOptionResponse,
} from '@agentclientprotocol/sdk/unstable';
import { ensure_cli_compatible } from '../../../conversation/backends/cli_compatibility.ts';
import { MODEL_CATALOG_PROBE_TIMEOUT_SECONDS } from '../../../conversation/snapshot/configuration.ts';
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
import { antigravity_driver } from '../driver.ts';
import type { AntigravityCatalogRead } from './contracts.ts';
import { antigravity_acp_launch, antigravity_requires_sign_in } from './flavor.ts';
import type { AntigravityLaunchOptions } from './flavor.ts';
import { remove_antigravity_session_files } from './session_files.ts';
import { select_option, select_values } from './status_readers.ts';

export class AntigravityModelCatalog {
  private readonly launch_options: Omit<AntigravityLaunchOptions, 'executable'>;
  private readonly child_processes: OwnedChildProcesses;
  private readonly one_shot_children: OwnedOneShotChildren;
  private readonly get_stop_options: (() => StopOptions | undefined) | undefined;
  private readonly shutting_down = new AbortController();
  private readonly reads = new Set<Promise<AntigravityCatalogRead>>();
  private stop_options: StopOptions | undefined;
  constructor(
    launch_options: Omit<AntigravityLaunchOptions, 'executable'>,
    dependencies: {
      child_processes: OwnedChildProcesses;
      one_shot_children: OwnedOneShotChildren;
      stop_options?: () => StopOptions | undefined;
    },
  ) {
    this.launch_options = launch_options;
    this.child_processes = dependencies.child_processes;
    this.one_shot_children = dependencies.one_shot_children;
    this.get_stop_options = dependencies.stop_options;
  }
  async read(executable: string, signal?: AbortSignal): Promise<AntigravityCatalogRead> {
    this.shutting_down.signal.throwIfAborted();
    const read = this.run(
      executable,
      AbortSignal.any([
        this.shutting_down.signal,
        AbortSignal.timeout(MODEL_CATALOG_PROBE_TIMEOUT_SECONDS * 1000),
        ...(signal ? [signal] : []),
      ]),
    );
    this.reads.add(read);
    try {
      return await read;
    } finally {
      this.reads.delete(read);
    }
  }
  /** Ends every read and waits until each has stopped its agy, within the shutdown deadline. */
  async shutdown(options?: StopOptions): Promise<void> {
    this.stop_options ??= options;
    if (!this.shutting_down.signal.aborted)
      this.shutting_down.abort(new Error('the Antigravity model catalog is shut down'));
    await Promise.allSettled([...this.reads]);
  }
  private async run(executable: string, signal: AbortSignal): Promise<AntigravityCatalogRead> {
    const cwd = await mkdtemp(join(tmpdir(), 'panels-antigravity-catalog-'));
    const launch = antigravity_acp_launch({ ...this.launch_options, executable });
    const environment = acp_child_environment(
      launch.environment_overrides,
      { role_materials: null },
      launch.inherited_environment,
    );
    let owned: OwnedProcess | null = null;
    let client: AcpClient | null = null;
    let session_id: string | null = null;
    try {
      // The card has just read `agy --version`, so this normally answers from that verdict.
      await ensure_cli_compatible(antigravity_driver.driver_kind, executable, {
        children: this.one_shot_children,
        signal,
        ...(this.launch_options.environment === undefined ? {} : { environment }),
      });
      owned = await spawn_owned_process(launch.argv, {
        child_processes: this.child_processes,
        on_created: (created) => {
          owned = created;
        },
        environment,
        working_directory: cwd,
      });
      // agy's log is not read, and must not fill its pipe.
      owned.child.stderr.resume();
      const connection = new AcpClient(
        ndJsonStream(
          Writable.toWeb(owned.child.stdin),
          Readable.toWeb(owned.child.stdout) as ReadableStream<Uint8Array>,
          { maxMessageBytes: CHILD_OUTPUT_LINE_LIMIT_BYTES },
        ),
      );
      client = connection;
      const request = <T>(method: string, params: unknown) =>
        connection.request<T>(method, params, { signal });
      await request('initialize', {
        protocolVersion: PROTOCOL_VERSION,
        clientInfo: { name: 'panels', title: 'Panels', version: '2.0.0' },
      });
      const session = await request<NewSessionResponse>('session/new', { cwd, mcpServers: [] });
      session_id = session.sessionId;
      const initial = session.configOptions ?? [];
      const after_selecting = new Map<string, readonly SessionConfigOption[]>();
      const model = select_option(initial, 'model');
      if (model !== undefined)
        for (const { value } of select_values(model)) {
          if (value === model.currentValue) continue;
          try {
            const selected = await request<SetSessionConfigOptionResponse>(
              'session/set_config_option',
              { sessionId: session_id, configId: model.id, value },
            );
            after_selecting.set(value, selected.configOptions);
          } catch (error) {
            // agy refused this one model. The others still count.
            if (!(error instanceof RequestError)) throw error;
          }
        }
      return { signed_in: true, initial, after_selecting };
    } catch (error) {
      if (antigravity_requires_sign_in(error) !== null) return { signed_in: false };
      throw error;
    } finally {
      client?.close();
      try {
        // Only a stopped agy has finished writing the files this removes.
        await (owned as OwnedProcess | null)?.stop(this.stop_options ?? this.get_stop_options?.());
        if (session_id !== null)
          await remove_antigravity_session_files(this.launch_options.home, session_id);
      } catch (error) {
        console.warn('the Antigravity model catalog did not clean up its session', error);
      }
      await rm(cwd, { recursive: true, force: true }).catch((error: unknown) =>
        console.warn('the Antigravity model catalog did not remove its directory', error),
      );
    }
  }
}
