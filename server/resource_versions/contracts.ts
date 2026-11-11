import type { FastifyRequest } from 'fastify';
import type { ReadSql } from '../core/database/contracts.ts';
export interface ResourceVersion { key: string; revision: string | null }
export interface ResourceVersionsResponse { epoch: string; resources: ResourceVersion[] }
export interface ResourceBodyResponse { epoch: string; key: string; revision: string; body: unknown }
export interface ResourceSnapshot { revision: string; read(): unknown }
export interface ResourceProvider {
  capture(sql: ReadSql, request: FastifyRequest, key: string): ResourceSnapshot | null;
}
