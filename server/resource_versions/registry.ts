import { ChiefResources } from '../worker_settings/resource_versions.ts';
import type { FastifyRequest } from 'fastify';
import { TicketWorkspaceResources } from '../tickets/resource_versions.ts';
import { WorkerTypeResources } from '../worker_types/resource_versions.ts';
import type { ReadSql } from '../core/database/contracts.ts';
import type { ResourceProvider, ResourceSnapshot } from './contracts.ts';
/** Later domains add providers at the composition point, without changes to these routes. */
export const providers: readonly ResourceProvider[] = [
  new WorkerTypeResources(),
  new TicketWorkspaceResources(),
  new ChiefResources(),
];
export function capture(
  providers: readonly ResourceProvider[],
  sql: ReadSql,
  request: FastifyRequest,
  key: string,
): ResourceSnapshot | null {
  for (const provider of providers) {
    const snapshot = provider.capture(sql, request, key);
    if (snapshot !== null) return snapshot;
  }
  return null;
}
