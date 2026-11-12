import { MetricsOperationalError, QueryLimitError, SnapshotExpiredError } from './contracts.ts';
import type { MetricsErrorWire } from './contracts.ts';

export function error_wire(error: unknown): MetricsErrorWire {
  const value = error as Error & { code?: string };
  return { name: value.name, message: value.message, ...(value.code ? { code: value.code } : {}) };
}
export function error_from_wire(error: MetricsErrorWire): Error {
  if (error.name === 'SnapshotExpiredError') return new SnapshotExpiredError(error.message);
  if (error.name === 'QueryLimitError') return new QueryLimitError(error.message);
  if (error.name === 'OperationalError' || error.code?.startsWith('SQLITE_'))
    return new MetricsOperationalError(error.message);
  const restored = new Error(error.message);
  restored.name = error.name;
  if (error.code) Object.assign(restored, { code: error.code });
  return restored;
}
