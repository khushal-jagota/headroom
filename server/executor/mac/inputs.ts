/** Verified prompt inputs reuse the existing HTTP transfer and file worker owners. */
import { randomUUID } from 'node:crypto';
import { stat } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import type { ChildCommand } from '../wire.generated.ts';
import { contained_path, safe_component } from './files.ts';
import type { FilesService } from './files.ts';
import {
  ExecutorPaths,
  INPUT_DOWNLOAD_RETRY_INITIAL_SECONDS,
  INPUT_DOWNLOAD_RETRY_MAX_SECONDS,
} from './configuration.ts';
import type { MacFileTransport } from './transport/files.ts';
import { ExecutorHttpError, failure_name } from './transport/errors.ts';
export class InputDownloadRefused extends Error {}
export class MacInputs {
  private readonly transport: Pick<MacFileTransport, 'download_command_file'>;
  private readonly files: FilesService;
  private readonly paths: ExecutorPaths;
  constructor(
    transport: Pick<MacFileTransport, 'download_command_file'>,
    files: FilesService,
    paths: ExecutorPaths,
  ) {
    this.transport = transport;
    this.files = files;
    this.paths = paths;
  }
  async download_inputs(command: ChildCommand, signal: AbortSignal): Promise<void> {
    const args = command.arguments;
    if (args.kind !== 'write_prompt' && args.kind !== 'steer') return;
    const seen = new Set<string>();
    for (const content of [args.content, args.sender_content ?? []])
      for (const piece of content) {
        if (!('byte_count' in piece) || seen.has(piece.stored_file_id)) continue;
        seen.add(piece.stored_file_id);
        const conversation = safe_component(command.child.conversation_id);
        const target = contained_path(
          join(this.paths.root, 'artifacts/files/conversations'),
          `${conversation}/${safe_component(piece.stored_file_id)}`,
        );
        let delay = INPUT_DOWNLOAD_RETRY_INITIAL_SECONDS;
        for (;;) {
          signal.throwIfAborted();
          try {
            await this.download(command.command_id, piece.stored_file_id, target, signal);
            break;
          } catch (error) {
            if (signal.aborted) throw error;
            if (error instanceof ExecutorHttpError && [403, 404, 409].includes(error.status))
              throw new InputDownloadRefused(
                `input ${piece.stored_file_id} is no longer served (${error.status})`,
              );
            console.warn(
              `Input download failed command=${command.command_id}: ${failure_name(error)}`,
            );
            try {
              await sleep(delay * 1000, undefined, { signal });
            } catch (error) {
              if (
                signal.aborted &&
                error instanceof Error &&
                error.name === 'AbortError' &&
                error.cause === signal.reason
              )
                throw signal.reason;
              throw error;
            }
            delay = Math.min(delay * 2, INPUT_DOWNLOAD_RETRY_MAX_SECONDS);
          }
        }
      }
  }
  private async download(
    command_id: string,
    file_id: string,
    target: string,
    signal: AbortSignal,
  ): Promise<void> {
    const run = await this.files.open_directory(this.paths.run, { create: true });
    const name = '.input-' + randomUUID().replaceAll('-', '');
    const temporary = join(this.paths.run, name);
    let primary: unknown;
    try {
      const digest = await this.transport.download_command_file(
        command_id,
        file_id,
        temporary,
        signal,
      );
      const bytes = (await stat(temporary)).size;
      signal.throwIfAborted();
      await this.files.install_verified(temporary, target, digest, bytes);
    } catch (error) {
      primary = error;
      throw error;
    } finally {
      const cleanup = await Promise.allSettled([
        this.files.unlink_at(run, name),
        this.files.close_directory(run),
      ]);
      const failures = cleanup.flatMap((result) =>
        result.status === 'rejected' ? [result.reason] : [],
      );
      if (primary !== undefined && failures.length) failures.unshift(primary);
      if (failures.length === 1) throw failures[0];
      if (failures.length) throw new AggregateError(failures, 'Input download and cleanup failed.');
    }
  }
}
