/** One capture binds the deployment body and revision to the same file read. */
import type { FastifyRequest } from 'fastify';
import type { ReadSql } from '../../core/database/contracts.ts';
import type { ResourceProvider, ResourceSnapshot } from '../../resource_versions/contracts.ts';
import type { DeploymentStatus } from './contracts.ts';
import { canonical_json } from './codec.ts';
export class DeploymentResources implements ResourceProvider {
  private readonly status: () => DeploymentStatus;
  constructor(status: () => DeploymentStatus) {
    this.status = status;
  }
  capture(_sql: ReadSql, _request: FastifyRequest, key: string): ResourceSnapshot | null {
    if (key !== '/api/deployment-status') return null;
    const body = this.status();
    return { revision: canonical_json(body), read: () => body };
  }
}
