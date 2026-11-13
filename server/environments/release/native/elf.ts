/** Build-only ELF inspection. Binary layout belongs to readelf, not this module. */
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { open, readFile, stat } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import type { ElfDynamic } from '../contracts.ts';
import { FILE_CHUNK_BYTES } from '../configuration.ts';
import { run_release_command } from '../process.ts';
import { MACH_O_MAGIC, NATIVE_TOOL_SECONDS } from './configuration.ts';

export function parse_dynamic(output: string, program_header: string): ElfDynamic {
  const paths = (tag: string): string[] =>
    [...output.matchAll(new RegExp('\\(' + tag + '\\).*?\\[(.*?)\\]', 'g'))].flatMap((match) =>
      match[1].split(':'),
    );
  return {
    needed: [...output.matchAll(/\(NEEDED\).*?\[(.*?)\]/g)].map((match) => match[1]),
    rpath: paths('RPATH'),
    runpath: output.includes('(RUNPATH)') ? paths('RUNPATH') : null,
    interpreter: /Requesting program interpreter: (.*?)\]/.exec(program_header)?.[1] ?? null,
  };
}
export async function read_elf(path: string, flag: string): Promise<string> {
  return (
    await run_release_command(['readelf', flag, path], { timeout_seconds: NATIVE_TOOL_SECONDS })
  ).stdout;
}
export async function read_dynamic(path: string): Promise<ElfDynamic> {
  return parse_dynamic(await read_elf(path, '-dW'), await read_elf(path, '-lW'));
}
export function elf_executable(header: string, interpreter: string | null): boolean {
  return (
    interpreter !== null ||
    /Type:\s+EXEC\b|DYN \(Position-Independent Executable file\)/.test(header)
  );
}
export async function inactive_musl(
  path: string,
  needed: readonly string[],
  interpreter: string | null,
): Promise<boolean> {
  if (interpreter?.includes('ld-musl-') || needed.some((name) => name.startsWith('libc.musl-')))
    return true;
  if (!needed.includes('libc.so')) return false;
  const metadata = join(dirname(path), 'package.json');
  try {
    if (!(await stat(metadata)).isFile()) return false;
    const value: unknown = JSON.parse(
      new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await readFile(metadata)),
    );
    return (
      value !== null &&
      typeof value === 'object' &&
      'name' in value &&
      value.name === '@rollup/rollup-linux-x64-musl' &&
      'libc' in value &&
      Array.isArray(value.libc) &&
      value.libc.length === 1 &&
      value.libc[0] === 'musl'
    );
  } catch (error) {
    if (
      error instanceof Error &&
      'code' in error &&
      (error.code === 'ENOENT' || error.code === 'ENOTDIR')
    )
      return false;
    throw error;
  }
}
export async function native_format(path: string): Promise<'ELF' | 'PE' | 'Mach-O' | null> {
  const handle = await open(path, 'r');
  try {
    const bytes = Buffer.alloc(4);
    const { bytesRead } = await handle.read(bytes, 0, bytes.length, 0);
    const magic = bytes.subarray(0, bytesRead).toString('hex');
    if (magic === '7f454c46') return 'ELF';
    if (bytesRead >= 2 && bytes[0] === 0x4d && bytes[1] === 0x5a) return 'PE';
    return MACH_O_MAGIC.has(magic) ? 'Mach-O' : null;
  } finally {
    await handle.close();
  }
}
export async function hash_native_file(path: string): Promise<string> {
  const digest = createHash('sha256');
  for await (const chunk of createReadStream(path, { highWaterMark: FILE_CHUNK_BYTES }))
    digest.update(chunk);
  return digest.digest('hex');
}
