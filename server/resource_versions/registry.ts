import type { FastifyRequest } from 'fastify';
import type { ReadSql } from '../core/database/contracts.ts';
import type { ResourceProvider, ResourceSnapshot } from './contracts.ts';
/** Later domains add providers at the composition point, without changes to these routes. */
export const providers: readonly ResourceProvider[] = [];
export function capture(providers: readonly ResourceProvider[], sql: ReadSql, request: FastifyRequest, key: string): ResourceSnapshot | null {
  for (const provider of providers) {
    const snapshot = provider.capture(sql, request, key);
    if (snapshot !== null) return snapshot;
  }
  return null;
}
