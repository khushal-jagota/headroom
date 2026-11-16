/** Share compilation results while each Ticket keeps its own Xcode build directories. */
import { randomUUID } from 'node:crypto';
import { mkdir, rename, rm, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';

function configuration_path(path: string): string {
  const absolute = resolve(path);
  if (/["\r\n$\\]/.test(absolute))
    throw new Error('Xcode cache configuration path contains unsupported characters');
  return absolute;
}

export async function prepare_xcode_compilation_cache(
  executor_root: string,
  ticket_directory: string,
  inherited_configuration?: string,
): Promise<string> {
  const cache = configuration_path(join(executor_root, 'cache', 'xcode'));
  const configuration = configuration_path(
    join(ticket_directory, 'xcode-compilation-cache.xcconfig'),
  );
  const inherited = inherited_configuration
    ? `#include "${configuration_path(inherited_configuration)}"\n`
    : '';
  const text =
    inherited +
    'COMPILATION_CACHE_ENABLE_CACHING = YES\n' +
    `COMPILATION_CACHE_CAS_PATH = ${cache}\n` +
    'SWIFT_ENABLE_PREFIX_MAPPING = YES\n' +
    'CLANG_ENABLE_PREFIX_MAPPING = YES\n';
  await mkdir(cache, { recursive: true });
  await mkdir(ticket_directory, { recursive: true });
  const temporary = `${configuration}.${randomUUID()}.tmp`;
  try {
    await writeFile(temporary, text, { flag: 'wx' });
    await rename(temporary, configuration);
  } finally {
    await rm(temporary, { force: true });
  }
  return configuration;
}
