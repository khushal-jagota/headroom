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
  INPUT_DOWNLOAD_MAX_ATTEMPTS,
} from './configuration.ts';
import type { MacFileTransport } from './transport/files.ts';
import { ExecutorHttpError } from './transport/errors.ts';
import { failure_detail } from './failure_detail.ts';
export class InputDownloadRefused extends Error {}
/** Every stored file a prompt, steer, or answer names, images and files alike, once each. */
export function input_file_ids(command: ChildCommand): string[] {
  const args = command.arguments;
  const pieces =
    args.kind === 'answer_user_input'
      ? args.answers.flatMap((answer) => answer.attachments ?? [])
      : args.kind === 'write_prompt' || args.kind === 'steer'
        ? [...args.content, ...(args.sender_content ?? [])]
        : [];
  const ids = new Set<string>();
  for (const piece of pieces) if ('stored_file_id' in piece) ids.add(piece.stored_file_id);
  return [...ids];
}
export class MacInputs {
  private readonly transport: Pick<MacFileTransport, 'download_command_file'>;
  private readonly files: FilesService;
  private readonly paths: ExecutorPaths;
  private readonly on_failure: (error: unknown) => void;
  constructor(
    transport: Pick<MacFileTransport, 'download_command_file'>,
    files: FilesService,
    paths: ExecutorPaths,
    on_failure: (error: unknown) => void,
  ) {
    this.transport = transport;
    this.files = files;
    this.paths = paths;
    this.on_failure = on_failure;
  }
  async download_inputs(command: ChildCommand, signal: AbortSignal): Promise<void> {
    for (const stored_file_id of input_file_ids(command)) {
      const conversation = safe_component(command.child.conversation_id);
      const target = contained_path(
        join(this.paths.root, 'artifacts/files/conversations'),
        `${conversation}/${safe_component(stored_file_id)}`,
      );
      for (let attempt = 1; ; attempt++) {
        signal.throwIfAborted();
        try {
          await this.download(command.command_id, stored_file_id, target, signal);
          break;
        } catch (error) {
          if (signal.aborted) throw error;
          if (error instanceof ExecutorHttpError && [403, 404, 409].includes(error.status))
            throw new InputDownloadRefused(
              `input ${stored_file_id} is no longer served (${error.status})`,
            );
          if (attempt >= INPUT_DOWNLOAD_MAX_ATTEMPTS)
            throw new InputDownloadRefused(
              `Could not download input ${stored_file_id} after ${attempt} attempts: ${failure_detail(error)}`,
              { cause: error },
            );
          console.warn(
            `Input download failed command=${command.command_id}: ${failure_detail(error)}`,
          );
          try {
            await sleep(
              INPUT_DOWNLOAD_RETRY_INITIAL_SECONDS * 2 ** (attempt - 1) * 1000,
              undefined,
              { signal },
            );
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
      if (failures.length) {
        const error =
          failures.length === 1
            ? failures[0]
            : new AggregateError(failures, 'Input download and cleanup failed.');
        this.on_failure(error);
        throw error;
      }
    }
  }
}
