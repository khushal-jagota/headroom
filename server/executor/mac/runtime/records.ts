/** Runtime records use native JSON across the file-worker boundary. */
import { is_object } from '../../../environments/updates/codec.ts';
import { ExecutorValidationError } from '../../../executor/protocol_errors.ts';

/** The invocation's existing FilesService owns these byte operations and its actual cleanup. */
export interface RuntimeRecordIO {
  read_runtime_record(path: string): Promise<string | null>;
  atomic_runtime_record(path: string, text: string): Promise<void>;
  remove_runtime_record(path: string): Promise<void>;
}
export async function atomic_record(
  files: RuntimeRecordIO,
  path: string,
  value: Record<string, unknown>,
): Promise<void> {
  await files.atomic_runtime_record(path, JSON.stringify(value) + '\n');
}
export async function read_record(
  files: RuntimeRecordIO,
  path: string,
): Promise<Record<string, unknown> | null> {
  const text = await files.read_runtime_record(path);
  if (text === null) return null;
  const value = JSON.parse(text);
  if (!is_object(value)) throw new ExecutorValidationError('runtime state must be an object');
  return value;
}
