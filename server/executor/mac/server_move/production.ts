/** The move runner over this invocation's client, files worker and process owner. */
import { homedir } from 'node:os';
import { extract_app_archive } from '../../../environments/release/archive.ts';
import { MAC_HOME } from '../../../environments/release/contracts.ts';
import type { OwnedChildProcesses } from '../../../lifecycle/contracts.ts';
import type { ServerMovePlatform } from '../../../server_move/contracts.ts';
import { MachineServerLink } from '../../../server_link/machine.ts';
import type { FilesService } from '../files.ts';
import type { MacHttpClient } from '../transport/http.ts';
import { CONNECTORS } from '../transport/http.ts';
import type { MacHttpOptions } from '../transport/contracts.ts';
import { HttpSessions } from '../transport/sessions.ts';
import { create_update_session } from '../updates/controller.ts';
import { run_move_installer } from './installer.ts';
import { ServerMoveRequests } from './requests.ts';
import { MacServerMove } from './runner.ts';

export interface ProductionServerMoveOptions {
  readonly root: string;
  readonly machine_id: string;
  /** The launch address, which the saved server address replaces. */
  readonly panels_server_url: string;
  readonly http: MacHttpOptions | undefined;
  readonly client: MacHttpClient;
  readonly files: FilesService;
  readonly child_processes: OwnedChildProcesses;
}
export function production_server_move(options: ProductionServerMoveOptions): MacServerMove {
  const { client, child_processes } = options;
  const platform: ServerMovePlatform = process.platform === 'darwin' ? 'darwin' : 'linux';
  const tls = { tls: options.http?.tls };
  const session = (address: string) => new HttpSessions(address, CONNECTORS, tls);
  return new MacServerMove({
    root: options.root,
    machine_id: options.machine_id,
    platform,
    home: homedir(),
    files: options.files,
    authentication_headers: () => client.authentication_headers(),
    server_address: () => client.server_url,
    server_session: () =>
      create_update_session(client.server_url, {
        ...options.http,
        server_link: client.server_link,
      }),
    session,
    // The running sessions read this saved address on their next request.
    save_server_address: (address, signal) =>
      new MachineServerLink(options.root, options.panels_server_url).prepare(async (origin) => {
        const probe = session(origin);
        try {
          return await new ServerMoveRequests(probe, () => ({}), signal).location();
        } finally {
          await probe.close();
        }
      }, address),
    extract: async (archive, destination, server_package, signal) => {
      await extract_app_archive(
        archive,
        destination,
        server_package.archive_sha256,
        server_package.revision,
        child_processes,
        undefined,
        { child_processes, signal, deadline: null },
        server_package.platform === 'darwin' ? MAC_HOME : undefined,
      );
    },
    installer: (payload, request, signal) =>
      run_move_installer({ child_processes, environment: process.env }, payload, request, signal),
    log: (message) => console.warn(message),
  });
}
