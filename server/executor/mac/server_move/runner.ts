/**
 * The target machine's side of one server move. A lease notice starts the run once. The run
 * copies the old server's state, installs the new server around it, proves it, and switches.
 * Each step is saved in `<executor root>/server-move/<move_id>/run.json`, so a restart either
 * finishes a committed switch or removes what the run made.
 */
import { lstat, mkdir, readdir, rm, stat } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { path_is_within } from '../../../core/path_containment.ts';
import { remove_owned_tree } from '../../../environments/release/archive.ts';
import {
  manifests_equal,
  relative_manifest,
  sha256_file,
} from '../../../environments/server_state/checksums.ts';
import type { ChecksumManifest } from '../../../environments/server_state/contracts.ts';
import type {
  ServerMoveInstallRequest,
  ServerMoveInstruction,
  ServerMovePackage,
  ServerMovePlatform,
  ServerMoveRemoveRequest,
} from '../../../server_move/contracts.ts';
import {
  SERVER_MOVE_DEFAULT_SERVICE,
  SERVER_MOVE_PAUSE_LIMIT_SECONDS,
} from '../../../server_move/contracts.ts';
import { ExecutorHttpError } from '../transport/errors.ts';
import type { MachineServerRecord } from '../../../server_link/contracts.ts';
import { parse_server_location } from '../../../server_link/logic.ts';
import { is_record } from '../../../core/records.ts';
import type { ServerMoveNotice } from '../../wire.generated.ts';
import type { FilesService } from '../files.ts';
import type { HttpSessions } from '../transport/sessions.ts';
import { TRANSFER_CHUNK_BYTES } from '../configuration.ts';
import { failure_detail } from '../failure_detail.ts';
import { atomic_record, read_record } from '../runtime/records.ts';
import {
  SERVER_MOVE_DATA_DIRECTORY,
  SERVER_MOVE_INSTRUCTION_POLL_SECONDS,
  SERVER_MOVE_PACKAGE_FILE,
  SERVER_MOVE_PAYLOAD_DIRECTORY,
  SERVER_MOVE_RECOVERY_RETRY_SECONDS,
  SERVER_MOVE_RUN_FILE,
  SERVER_MOVE_SETTLE_MARGIN_SECONDS,
  SERVER_MOVE_WORK_DIRECTORY,
} from './configuration.ts';
import type { ServerMoveRunRecord } from './contracts.ts';
import { ServerMoveFailure } from './contracts.ts';
import {
  extra_staged_files,
  failure_reason,
  install_request,
  package_problem,
  parse_install_result,
  parse_instruction,
  server_install_root,
  valid_move_id,
} from './logic.ts';
import { SERVER_FILE_SHA256_HEADER, ServerMoveRequests } from './requests.ts';

export interface ServerMoveRunnerOptions {
  /** The executor root. */
  readonly root: string;
  /** This machine's id: the held marker's target machine. */
  readonly machine_id: string;
  readonly platform: ServerMovePlatform;
  /** The account home, for the platform default installation root. */
  readonly home: string;
  readonly files: Pick<
    FilesService,
    | 'open_transfer'
    | 'write_transfer'
    | 'finish_transfer'
    | 'close_transfer'
    | 'read_runtime_record'
    | 'atomic_runtime_record'
    | 'remove_runtime_record'
  >;
  /** This machine's own credential headers. */
  authentication_headers(): Readonly<Record<string, string>>;
  /** The current server address. */
  server_address(): string;
  /** A session to the current server address that sends and checks the saved server identity. */
  server_session(): HttpSessions;
  /** A plain session to one address. It sends no saved server identity. */
  session(address: string): HttpSessions;
  /** Probes the address and saves it as this machine's server address. */
  save_server_address(address: string, signal: AbortSignal): Promise<MachineServerRecord>;
  /** Safe extraction of the checked package into a folder that does not exist yet. */
  extract(
    archive: string,
    destination: string,
    server_package: ServerMovePackage,
    signal: AbortSignal,
  ): Promise<void>;
  /** Runs the payload's installer and returns its standard output. */
  installer(
    payload: string,
    request: ServerMoveInstallRequest | ServerMoveRemoveRequest,
    signal: AbortSignal,
  ): Promise<string>;
  log(message: string): void;
}
const missing = (error: unknown) =>
  ['ENOENT', 'ENOTDIR'].includes((error as NodeJS.ErrnoException).code ?? '');
async function exists(path: string): Promise<boolean> {
  try {
    await lstat(path);
    return true;
  } catch (error) {
    if (missing(error)) return false;
    throw error;
  }
}
/** Relative names of every entry that is not a directory, below `root`. */
async function staged_names(root: string, prefix = ''): Promise<string[]> {
  const names: string[] = [];
  for (const entry of await readdir(join(root, prefix), { withFileTypes: true })) {
    const relative = prefix + entry.name;
    if (entry.isDirectory()) names.push(...(await staged_names(root, relative + '/')));
    else names.push(relative);
  }
  return names;
}
function parse_run_record(value: Record<string, unknown> | null): ServerMoveRunRecord | null {
  if (value === null) return null;
  const instruction = parse_instruction(value.instruction);
  const installation = value.installation;
  if (
    instruction.package === null ||
    typeof value.old_address !== 'string' ||
    typeof value.committed !== 'boolean' ||
    !(value.checked_at_ms === null || Number.isSafeInteger(value.checked_at_ms)) ||
    !(
      installation === null ||
      (is_record(installation) &&
        typeof installation.root === 'string' &&
        typeof installation.service === 'string' &&
        (installation.executor_link === null || typeof installation.executor_link === 'string'))
    )
  )
    throw new ServerMoveFailure('The saved move record is invalid.');
  return value as unknown as ServerMoveRunRecord;
}

export class MacServerMove {
  private readonly options: ServerMoveRunnerOptions;
  private readonly cancellation = new AbortController();
  /** Moves this process started. A notice starts each one at most once. */
  private readonly started_runs = new Set<string>();
  private task: Promise<void> | undefined;
  private retry: ReturnType<typeof setTimeout> | undefined;
  /** Move folders may remain from a restart or a failed cleanup. */
  private recovery_needed = true;
  private started = false;
  private closed = false;
  constructor(options: ServerMoveRunnerOptions) {
    this.options = options;
  }
  private get signal(): AbortSignal {
    return this.cancellation.signal;
  }
  private get work_root(): string {
    return join(this.options.root, SERVER_MOVE_WORK_DIRECTORY);
  }
  /** Begins with the folders that an earlier process left. */
  start(): void {
    if (this.closed) throw new ServerLifecycleError('Server move runner is stopped.');
    this.started = true;
    this.schedule(null);
  }
  notice(notice: ServerMoveNotice | null): void {
    if (this.started && !this.closed) this.schedule(notice);
  }
  private schedule(notice: ServerMoveNotice | null): void {
    if (this.task !== undefined || this.closed) return;
    let work: () => Promise<void>;
    if (this.recovery_needed) work = () => this.recover();
    else if (notice !== null && valid_move_id(notice.move_id)) {
      const move_id = notice.move_id;
      if (this.started_runs.has(move_id)) return;
      this.started_runs.add(move_id);
      work = () => this.run(move_id);
    } else return;
    const task = work()
      .catch((error: unknown) => {
        if (!this.signal.aborted) this.options.log(`Server move: ${failure_detail(error)}`);
      })
      .finally(() => {
        if (this.task === task) this.task = undefined;
        if (this.recovery_needed && !this.closed) {
          clearTimeout(this.retry);
          this.retry = setTimeout(
            () => this.schedule(null),
            SERVER_MOVE_RECOVERY_RETRY_SECONDS * 1000,
          );
        }
      });
    this.task = task;
  }
  private requests(session: HttpSessions): ServerMoveRequests {
    return new ServerMoveRequests(
      session,
      () => this.options.authentication_headers(),
      this.signal,
    );
  }
  private async with_requests<T>(
    session: HttpSessions,
    work: (requests: ServerMoveRequests) => Promise<T>,
  ): Promise<T> {
    try {
      return await work(this.requests(session));
    } finally {
      await session.close();
    }
  }
  private save(work: string, record: ServerMoveRunRecord): Promise<void> {
    return atomic_record(
      this.options.files,
      join(work, SERVER_MOVE_RUN_FILE),
      record as unknown as Record<string, unknown>,
    );
  }
  /** The run's instruction once its package is ready, or null when the run is not active. */
  private async wait_for_package(
    requests: ServerMoveRequests,
    move_id: string,
  ): Promise<ServerMoveInstruction | null> {
    for (;;) {
      const instruction = await requests.instruction();
      if (
        instruction === null ||
        instruction.move_id !== move_id ||
        !['preparing', 'copying'].includes(instruction.phase)
      )
        return null;
      if (instruction.package !== null) return instruction;
      await sleep(SERVER_MOVE_INSTRUCTION_POLL_SECONDS * 1000, undefined, { signal: this.signal });
    }
  }
  /**
   * One download into a new file. The package has a known digest and size. A state file has
   * no announced size and carries its digest in a header, which must equal `sha256` when
   * given. A mismatch leaves no file.
   */
  private async receive(
    requests: ServerMoveRequests,
    path: string,
    target: string,
    expected: {
      readonly sha256: string | null;
      readonly size: number | null;
      readonly name: string;
    },
  ): Promise<string> {
    const { files } = this.options;
    return requests.download(path, async (response) => {
      let sha256 = expected.sha256;
      if (expected.size === null) {
        const header = response.headers[SERVER_FILE_SHA256_HEADER];
        if (typeof header !== 'string' || !/^[0-9a-f]{64}$/.test(header))
          throw new ServerMoveFailure(`The old server sent ${expected.name} without its digest.`);
        if (sha256 !== null && header !== sha256)
          throw new ServerMoveFailure(`${expected.name} changed after the complete list.`);
        sha256 = header;
      }
      const handle = await files.open_transfer(target, 'write');
      let complete = false,
        count = 0;
      try {
        for await (const data of response) {
          this.signal.throwIfAborted();
          const bytes = Buffer.from(data as Uint8Array);
          count += bytes.length;
          if (expected.size !== null && count > expected.size)
            throw new ServerMoveFailure(`${expected.name} is larger than announced.`);
          for (let offset = 0; offset < bytes.length; offset += TRANSFER_CHUNK_BYTES)
            await files.write_transfer(
              handle,
              bytes.subarray(offset, offset + TRANSFER_CHUNK_BYTES),
            );
        }
        try {
          await files.finish_transfer(handle, sha256!, expected.size ?? count);
        } catch (error) {
          throw new ServerMoveFailure(`${expected.name} does not match its digest.`, {
            cause: error,
          });
        }
        complete = true;
      } finally {
        await files.close_transfer(handle, !complete);
      }
      return sha256!;
    });
  }
  /**
   * Copies every listed file that is not staged with its digest. Before the pause it returns
   * the files it missed. In the pause every file must match the list, except a served file
   * under `files/`: machine code on the old machine still works there, so the file route's
   * digest replaces the listed one, and a 404 drops the path. The returned list is the one
   * the staged folder must equal.
   */
  private async stage(
    requests: ServerMoveRequests,
    move_id: string,
    data: string,
    manifest: ChecksumManifest,
    complete: boolean,
  ): Promise<{ readonly missed: number; readonly manifest: ChecksumManifest }> {
    let missed = 0;
    const staged_list: ChecksumManifest = Object.assign(Object.create(null), manifest);
    for (const [relative, digest] of Object.entries(manifest)) {
      const served = relative.startsWith('files/');
      const target = join(data, relative);
      if (!path_is_within(data, target))
        throw new ServerMoveFailure('The file list leaves its folder.');
      let staged: string | null = null;
      try {
        const entry = await lstat(target);
        if (entry.isFile()) staged = await sha256_file(target);
      } catch (error) {
        if (!missing(error)) throw error;
      }
      if (staged === digest) continue;
      try {
        await rm(target, { recursive: true, force: true });
        await mkdir(dirname(target), { recursive: true, mode: 0o700 });
        staged_list[relative] = await this.receive(
          requests,
          requests.file_path(move_id, relative),
          target,
          { sha256: complete && !served ? digest : null, size: null, name: `The file ${relative}` },
        );
      } catch (error) {
        if (this.signal.aborted) throw error;
        if (complete && served && error instanceof ExecutorHttpError && error.status === 404) {
          delete staged_list[relative];
          continue;
        }
        // Before the pause a file can change or go away. The pause copies it again.
        if (complete) throw error;
        missed++;
      }
    }
    return { missed, manifest: staged_list };
  }
  /**
   * Before the pause: the new root must not exist, and the installer's rename of the staged
   * folder into it must stay on one filesystem.
   */
  private async require_install_root(root: string): Promise<void> {
    if (await exists(root))
      throw new ServerMoveFailure(`A server is already installed at ${root}.`);
    let ancestor = dirname(root);
    while (!(await exists(ancestor)) && dirname(ancestor) !== ancestor)
      ancestor = dirname(ancestor);
    if ((await stat(ancestor)).dev !== (await stat(this.work_root)).dev)
      throw new ServerMoveFailure(
        `The new server folder ${root} is on another filesystem than ${this.work_root}.`,
      );
  }
  /** Removes staged files that the complete list no longer has, then checks the exact set. */
  private async check_staged(data: string, manifest: ChecksumManifest): Promise<void> {
    if (!manifests_equal(await relative_manifest(data, { strict: true }), manifest))
      throw new ServerMoveFailure('The copied files do not match the complete list.');
  }
  private async prune(data: string, manifest: ChecksumManifest): Promise<void> {
    for (const relative of extra_staged_files(await staged_names(data), manifest))
      await rm(join(data, relative), { force: true });
  }
  private async run(move_id: string): Promise<void> {
    const { log } = this.options;
    const work = join(this.work_root, move_id);
    const data = join(work, SERVER_MOVE_DATA_DIRECTORY),
      payload = join(work, SERVER_MOVE_PAYLOAD_DIRECTORY),
      archive = join(work, SERVER_MOVE_PACKAGE_FILE);
    let record: ServerMoveRunRecord | undefined,
      commit_refused = false;
    try {
      record = await this.with_requests(this.options.server_session(), async (old) => {
        const instruction = await this.wait_for_package(old, move_id);
        if (instruction === null) {
          log(`Server move ${move_id}: the old server has no active run for it.`);
          return undefined;
        }
        const server_package = instruction.package!;
        const problem = package_problem(server_package, this.options.platform);
        if (problem !== null) throw new ServerMoveFailure(problem);
        let current: ServerMoveRunRecord = {
          instruction: { ...instruction, package: server_package },
          old_address: this.options.server_address(),
          installation: null,
          checked_at_ms: null,
          committed: false,
        };
        await mkdir(this.work_root, { recursive: true, mode: 0o700 });
        await mkdir(work, { mode: 0o700 });
        await mkdir(data, { mode: 0o700 });
        record = current;
        await this.save(work, current);
        log(`Server move ${move_id}: downloading the server package.`);
        await this.receive(old, old.package_path(move_id), archive, {
          sha256: server_package.archive_sha256,
          size: server_package.archive_size,
          name: 'The server package',
        });
        // Extraction runs before the pause, so the pause holds only the copy and the check.
        await this.options.extract(archive, payload, server_package, this.signal);
        const listed = await old.files(move_id);
        const { missed } = await this.stage(old, move_id, data, listed.manifest, false);
        const root = server_install_root(this.options.platform, this.options.home);
        await this.require_install_root(root);
        log(
          `Server move ${move_id}: copied ${Object.keys(listed.manifest).length - missed} files while the server runs; asking it to pause.`,
        );
        const complete = await old.pause(move_id);
        await this.prune(data, complete.manifest);
        const staged = await this.stage(old, move_id, data, complete.manifest, true);
        await this.check_staged(data, staged.manifest);
        log(`Server move ${move_id}: the copied files match the complete list. Installing.`);
        if (await exists(root))
          throw new ServerMoveFailure(`A server is already installed at ${root}.`);
        current = {
          ...current,
          installation: { root, service: SERVER_MOVE_DEFAULT_SERVICE, executor_link: null },
        };
        record = current;
        await this.save(work, current);
        const output = await this.options.installer(
          payload,
          install_request(
            current,
            { root, archive, data_directory: data },
            this.options.machine_id,
          ),
          this.signal,
        );
        let answer: unknown;
        try {
          answer = JSON.parse(output);
        } catch {
          throw new ServerMoveFailure('The installer gave an answer that is not JSON.');
        }
        const result = parse_install_result(answer, root);
        current = {
          ...current,
          installation: { ...current.installation!, executor_link: result.executor_link },
        };
        record = current;
        await this.save(work, current);
        await this.with_requests(this.options.session(result.executor_link), async (held) => {
          await held.answer_probe_job(await held.probe_job());
        });
        log(`Server move ${move_id}: the new server is healthy and answered the test job.`);
        current = { ...current, checked_at_ms: Date.now() };
        record = current;
        await this.save(work, current);
        const reply = await old.report(move_id, { state: 'checked' });
        if (!reply.commit) {
          commit_refused = true;
          throw new ServerMoveFailure('The old server did not commit the move.');
        }
        current = { ...current, committed: true };
        await this.save(work, current);
        return current;
      });
    } catch (error) {
      if (this.signal.aborted) return;
      if (record !== undefined && record.checked_at_ms !== null && !commit_refused) {
        // The old server may be past the point of no return. Its phase decides.
        log(`Server move ${move_id}: the commit answer is uncertain: ${failure_detail(error)}`);
        await this.follow_old_server(work, record);
        return;
      }
      const reason = failure_reason(error);
      log(`Server move ${move_id} failed: ${reason}`);
      if (record !== undefined) await this.fail(work, record, reason);
      else
        await this.with_requests(this.options.server_session(), (old) =>
          old.report(move_id, { state: 'failed', reason }),
        ).catch((report: unknown) =>
          log(
            `Server move ${move_id}: the failure report did not arrive: ${failure_detail(report)}`,
          ),
        );
      return;
    } finally {
      this.recovery_needed = true;
    }
    if (record !== undefined) await this.switch(work, record);
  }
  /**
   * Tell the old server, then remove what this run made. An old server already past the point
   * of no return answers commit: true, and the run switches instead.
   */
  private async fail(work: string, record: ServerMoveRunRecord, reason: string): Promise<void> {
    const move_id = record.instruction.move_id;
    let commit = false;
    try {
      commit = (
        await this.with_requests(this.options.server_session(), (old) =>
          old.report(move_id, { state: 'failed', reason }),
        )
      ).commit;
    } catch (error) {
      // After `checked` an unanswered report proves nothing. The folder stays for recovery.
      if (this.signal.aborted || record.checked_at_ms !== null) throw error;
      this.options.log(
        `Server move ${move_id}: the failure report did not arrive: ${failure_detail(error)}`,
      );
    }
    if (commit) return this.commit_and_switch(work, record);
    await this.remove(work, record);
  }
  private async commit_and_switch(work: string, record: ServerMoveRunRecord): Promise<void> {
    if (!record.installation?.executor_link)
      throw new ServerMoveFailure(
        'The old server committed a move that this machine did not install.',
      );
    const committed = { ...record, committed: true };
    await this.save(work, committed);
    return this.switch(work, committed);
  }
  /**
   * After `checked` without a clear answer: the old server's phase decides. Committing or
   * moved switches, failed or gone cleans up, and a pause that outlives its limit is reported.
   */
  private async follow_old_server(work: string, record: ServerMoveRunRecord): Promise<void> {
    const move_id = record.instruction.move_id;
    const limit =
      record.checked_at_ms! +
      (SERVER_MOVE_PAUSE_LIMIT_SECONDS + SERVER_MOVE_SETTLE_MARGIN_SECONDS) * 1000;
    for (;;) {
      const instruction = await this.with_requests(this.options.server_session(), (old) =>
        old.instruction(),
      );
      const same = instruction !== null && instruction.move_id === move_id;
      if (same && ['committing', 'moved'].includes(instruction.phase))
        return this.commit_and_switch(work, record);
      if (!same || instruction.phase === 'failed') return this.remove(work, record);
      if (Date.now() >= limit)
        return this.fail(work, record, 'The old server did not settle the move after the check.');
      await sleep(SERVER_MOVE_INSTRUCTION_POLL_SECONDS * 1000, undefined, { signal: this.signal });
    }
  }
  private async remove(work: string, record: ServerMoveRunRecord | null): Promise<void> {
    if (record?.committed) throw new Error('A committed server move is never removed.');
    const installation = record?.installation;
    if (installation && (await exists(installation.root))) {
      await this.options.installer(
        join(work, SERVER_MOVE_PAYLOAD_DIRECTORY),
        { command: 'remove', root: installation.root, service: installation.service },
        this.signal,
      );
      this.options.log(`Server move: removed the new server at ${installation.root}.`);
    }
    if (await exists(work)) await remove_owned_tree(work);
    this.options.log(`Server move: removed ${work}.`);
  }
  /**
   * After commit: S_B takes the next move number, this machine saves S_B's local link as its
   * server address, and the old server hears the last notice. Each step can repeat.
   */
  private async switch(work: string, record: ServerMoveRunRecord): Promise<void> {
    const { instruction, installation } = record;
    const link = installation?.executor_link;
    if (!link) throw new ServerMoveFailure('The committed move has no new server link.');
    const next = instruction.move_number + 1;
    await this.with_requests(this.options.session(link), async (held) => {
      const location = parse_server_location(await held.location());
      if (location.server_id !== instruction.server_id || location.state !== 'active')
        throw new ServerMoveFailure('The new server answers for another server.');
      if (location.move_number === instruction.move_number) {
        const reply = await held.commit(instruction.move_id);
        if (reply.server_id !== instruction.server_id || reply.move_number !== next)
          throw new ServerMoveFailure('The new server did not take the next move number.');
      } else if (location.move_number !== next)
        throw new ServerMoveFailure('The new server has an unexpected move number.');
    });
    const saved = await this.options.save_server_address(link, this.signal);
    if (saved.server_url !== link || saved.highest_move_number !== next)
      throw new ServerMoveFailure('This machine did not switch to the new server.');
    this.options.log(`Server move ${instruction.move_id}: this machine now uses the new server.`);
    // The old address, without the new identity: the old server refuses a newer move number.
    await this.with_requests(this.options.session(record.old_address), (old) =>
      old.switched(instruction.move_id),
    ).catch((error: unknown) => {
      if (this.signal.aborted) throw error;
      this.options.log(
        `Server move ${instruction.move_id}: the old server did not take the switch notice: ${failure_detail(error)}`,
      );
    });
    // The installer moved the staged data. The package and payload are no longer needed.
    await remove_owned_tree(work);
  }
  /** One leftover move folder: finish its switch, or remove it when its run cannot go on. */
  private async settle(move_id: string): Promise<void> {
    const work = join(this.work_root, move_id);
    const record = parse_run_record(
      await read_record(this.options.files, join(work, SERVER_MOVE_RUN_FILE)),
    );
    if (record === null) return this.remove(work, null);
    if (record.committed) return this.switch(work, record);
    if (record.checked_at_ms !== null) return this.follow_old_server(work, record);
    const instruction = await this.with_requests(this.options.server_session(), (old) =>
      old.instruction(),
    );
    const same = instruction !== null && instruction.move_id === move_id;
    if (same && ['committing', 'moved'].includes(instruction.phase))
      return this.commit_and_switch(work, record);
    if (same && instruction.phase !== 'failed')
      return this.fail(work, record, 'The machine code restarted during the move.');
    return this.remove(work, record);
  }
  private async recover(): Promise<void> {
    this.recovery_needed = false;
    let names: string[];
    try {
      names = await readdir(this.work_root);
    } catch (error) {
      if (missing(error)) return;
      throw error;
    }
    for (const name of names) {
      if (!valid_move_id(name)) continue;
      try {
        await this.settle(name);
      } catch (error) {
        if (this.signal.aborted) throw error;
        this.recovery_needed = true;
        this.options.log(`Server move ${name}: cleanup waits: ${failure_detail(error)}`);
      }
    }
  }
  stop(options: StopOptions): Promise<void> {
    this.closed = true;
    clearTimeout(this.retry);
    this.cancellation.abort(new DOMException('Server move stopped', 'AbortError'));
    return observe_shutdown(this.settled(), options, 'The server move remains unfinished.');
  }
  async settled(): Promise<void> {
    await this.task;
  }
}
