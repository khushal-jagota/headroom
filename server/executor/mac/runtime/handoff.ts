/** Planned swaps reuse the canonical executor identity check and the durable handoff record. */
import type { ExecutorIdentity } from '../../../execution_identity.ts';
import { parse_identity } from '../../../execution_identity.ts';
import { integer_value } from '../../integers.ts';
import { read_record } from './records.ts';
import type { RuntimeRecordIO } from './records.ts';

export async function handoff_identity(
  identity: ExecutorIdentity,
  files: RuntimeRecordIO,
  path: string | null,
): Promise<ExecutorIdentity> {
  if (path === null) return identity;
  const record = await read_record(files, path);
  if (record === null) throw new Error('Planned update lacks its durable executor handoff.');
  const { executor_incarnation, connection_generation = 0 } = record;
  const next = parse_identity(
    { ...identity, executor_incarnation, connection_generation },
    { check_versions: false },
  );
  if (integer_value(next.connection_generation) < 0n)
    throw new TypeError('Invalid handoff generation.');
  return next;
}
export function handoff_record(
  identity: ExecutorIdentity,
  replay_pending: boolean,
): Record<string, unknown> {
  return {
    executor_incarnation: identity.executor_incarnation,
    connection_generation: identity.connection_generation,
    replay_pending,
  };
}
