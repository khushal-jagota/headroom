/** Fresh Python comparison companion. This module starts no service or connection. */
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { ExecutorValidationError } from '../../protocol.ts';
import { join } from 'node:path';
import { FilesService, contained_path, file_version, safe_component } from '../files.ts';
import { executor_paths, validate_server_url } from '../configuration.ts';
import * as configuration from '../configuration.ts';
function result(action: () => unknown): unknown {
  try {
    return { value: action() };
  } catch (error) {
    if (!(error instanceof ExecutorValidationError)) throw error;
    return { error: { kind: 'validation', message: error.message } };
  }
}
export async function files_parity(input: {
  root: string;
  urls: string[];
  components: string[];
  relatives: string[];
  roots: string[];
  refused_roots: string[];
}): Promise<unknown> {
  const root = join(input.root, 'ts'),
    source = join(root, 'source'),
    service = new FilesService();
  try {
    const [sha, count] = await service.file_digest(source),
      copy = join(root, 'copy');
    await service.copy_verified(source, copy, sha, count);
    const target = join(root, 'nested', 'target');
    await service.install_verified(copy, target, sha, count);
    const version = file_version(target),
      verified = await service.verified_version(target, sha, count);
    let corrupt_refused = false;
    try {
      await service.copy_verified(source, join(root, 'bad-copy'), '0'.repeat(64), count);
    } catch {
      corrupt_refused = true;
    }
    const old = readFileSync(target);
    try {
      await service.install_verified(source, target, sha, count - 1);
    } catch {
      /* Preserve original. */
    }
    let fifo_refused = false;
    try {
      await service.file_digest(join(root, 'fifo'));
    } catch (error) {
      fifo_refused = error instanceof Error && error.message === 'artifact is not regular file';
    }
    const paths = input.roots.map((configured) => {
      const resolved = executor_paths({ ...process.env, PLAN_EXECUTOR_ROOT: configured });
      return [
        resolved.root,
        resolved.workspace,
        resolved.spool,
        resolved.artifacts,
        resolved.message_files_anchor,
        resolved.skills,
        resolved.run,
        resolved.ipc_socket,
      ];
    });
    return {
      settings: Object.fromEntries(
        Object.entries(configuration).filter(([name]) => /^[A-Z_]+$/.test(name)),
      ),
      digest: [sha, count],
      copied: readFileSync(copy).equals(readFileSync(source)),
      installed: await service.file_digest(target),
      verified: version !== null && verified !== null && version.every((v, i) => v === verified[i]),
      corrupt_refused,
      corrupt_cleaned: !existsSync(join(root, 'bad-copy')),
      old_preserved: old.equals(readFileSync(target)),
      files: readdirSync(join(root, 'nested')).sort(),
      fifo_refused,
      components: input.components.map((value) => result(() => safe_component(value))),
      relatives: input.relatives.map((value) =>
        result(() => contained_path(root, value).slice(root.length)),
      ),
      urls: input.urls.map((value) => result(() => validate_server_url(value))),
      paths,
      refused_roots: input.refused_roots.map((configured) =>
        result(() => executor_paths({ ...process.env, PLAN_EXECUTOR_ROOT: configured })),
      ),
      refused_homes: ['', 'relative'].map((HOME) => result(() => executor_paths({ HOME }))),
    };
  } finally {
    await service.stop({ deadline: performance.now() / 1000 + 10 });
    await service.settled();
  }
}
