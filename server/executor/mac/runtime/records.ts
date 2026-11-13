/** Runtime records keep Python's bytes and numeric kinds across the file-worker boundary. */
import { stringify_python_json } from '../../../core/http/numeric_json.ts';
import { is_object, parse_attempt_json } from '../../../environments/updates/codec.ts';
import { ValueError } from '../../../core/value_error.ts';

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
  await files.atomic_runtime_record(
    path,
    stringify_python_json(value, { sort_keys: true, ensure_ascii: true }) + '\n',
  );
}
export async function read_record(
  files: RuntimeRecordIO,
  path: string,
): Promise<Record<string, unknown> | null> {
  const text = await files.read_runtime_record(path);
  if (text === null) return null;
  const value = parse_attempt_json(text);
  if (!is_object(value)) throw new ValueError('runtime state must be an object');
  return value;
}
