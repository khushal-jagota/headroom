/** A machine credential stays private and outside versioned runtimes and journals. */
import { randomUUID } from 'node:crypto';
import { mkdir, open, rename, unlink } from 'node:fs/promises';
import type { FileHandle } from 'node:fs/promises';
import { basename, dirname, join } from 'node:path';
import { ExecutorValidationError } from './protocol_errors.ts';

const MAX_CHARACTERS = 4096;
const MAX_READ_BYTES = (MAX_CHARACTERS + 1) * 4;
const CREDENTIAL = /^[A-Za-z0-9_-]{43}$/u;

export function credential_path(root: string): string {
  return join(root, 'machine-credential.json');
}
function valid_credential(value: unknown): value is string {
  return typeof value === 'string' && value.length === 43 && CREDENTIAL.test(value);
}
export async function read_credential(path: string): Promise<string | null> {
  let file: FileHandle;
  try {
    file = await open(path, 'r');
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
    throw error;
  }
  try {
    const bytes = Buffer.alloc(MAX_READ_BYTES);
    let count = 0;
    while (count < bytes.length) {
      const { bytesRead } = await file.read(bytes, count, bytes.length - count, null);
      if (bytesRead === 0) break;
      count += bytesRead;
    }
    const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true })
      .decode(bytes.subarray(0, count))
      .replace(/\r\n?/g, '\n');
    if (count === bytes.length || Array.from(text).length > MAX_CHARACTERS)
      throw new ExecutorValidationError('The machine credential file is invalid');
    const value: unknown = JSON.parse(text);
    const credential =
      value !== null && typeof value === 'object' && !Array.isArray(value) && 'credential' in value
        ? value.credential
        : undefined;
    if (!valid_credential(credential))
      throw new ExecutorValidationError('The machine credential file is invalid');
    return credential;
  } finally {
    await file.close();
  }
}
export async function write_credential(path: string, credential: string): Promise<void> {
  if (!valid_credential(credential))
    throw new ExecutorValidationError('The machine credential is invalid');
  const parent = dirname(path);
  await mkdir(parent, { recursive: true, mode: 0o700 });
  const temporary = join(parent, `.${basename(path)}.${randomUUID().replaceAll('-', '')}`);
  try {
    const file = await open(temporary, 'wx', 0o600);
    try {
      await file.writeFile(JSON.stringify({ credential }) + '\n');
      await file.sync();
    } finally {
      await file.close();
    }
    await rename(temporary, path);
    const directory = await open(parent, 'r');
    try {
      await directory.sync();
    } finally {
      await directory.close();
    }
  } finally {
    await unlink(temporary).catch((error: unknown) => {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    });
  }
}
