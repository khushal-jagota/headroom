import type { FastifyRequest } from 'fastify';
import { PlannerError } from '../contracts.ts';
import type { Principal } from '../contracts.ts';
import { requestContext } from './identity.ts';
import {
  chainFacts,
  isAbove,
  requireAbove,
  requireAboveOrSelf,
  requireSelf,
  requireInChain,
  refuse,
  refuseManagerReparenting,
  standsAbove,
} from './service.ts';
import type { AuthoritySql, Target } from './service.ts';
function object(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
function principal(raw: unknown): Principal {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw))
    throw new PlannerError('validation', 'target must be a principal object');
  const value = object(raw),
    kind = value.kind;
  if (typeof kind !== 'string' || !['owner', 'chief', 'manager', 'ticket', 'goal'].includes(kind))
    throw new PlannerError(
      'validation',
      'recipient kind must be owner, chief, manager, or ticket',
      { kind: kind ?? null },
    );
  if (kind === 'goal')
    throw new PlannerError('validation', 'unknown message recipient kind', { kind });
  if (typeof value.id !== 'string' || !value.id.trim())
    throw new PlannerError('validation', `${kind} recipient id is required`, {
      id: value.id ?? null,
    });
  const id = value.id.trim();
  if ((kind === 'owner' || kind === 'chief') && id !== kind)
    throw new PlannerError('validation', `${kind} principal id must be ${kind}`, { id: value.id });
  return { kind, id } as Principal;
}
export interface AdmissionDependencies {
  /** Supplied by the measure area when it registers its routes. */
  sourceResponsible?: (sql: AuthoritySql, sourceId: string) => Principal;
  measureSource?: (sql: AuthoritySql, kind: 'measures' | 'datasets', id: string) => string | null;
  managerManualMeasure?: (sql: AuthoritySql, managerId: string, measureId: string) => boolean;
  parseSourceResponsible?: (value: unknown) => Principal;
}
function required<T>(value: T | undefined, name: string): T {
  if (!value) throw new Error(`Authority dependency ${name} is not composed`);
  return value;
}
export function admitMessage(sql: AuthoritySql, caller: Principal, recipient: Principal): void {
  if (caller.kind === 'manager' && recipient.kind === 'manager') {
    for (const id of [caller.id, recipient.id])
      if (!sql.get('SELECT 1 FROM managers WHERE id=?', [id]))
        throw new PlannerError('not_found', 'manager not found', { id });
    return;
  }
  if (recipient.kind === 'owner' || recipient.kind === 'chief') return;
  const target: Target = { kind: recipient.kind, id: recipient.id };
  if (isAbove(sql, caller, target)) return;
  if (
    caller.kind !== 'owner' &&
    caller.kind !== 'chief' &&
    isAbove(sql, recipient, { kind: caller.kind, id: caller.id })
  )
    return;
  requireAbove(sql, caller, target);
}
function admitSource(sql: AuthoritySql, caller: Principal, responsible: Principal): void {
  if (responsible.kind === 'goal')
    return requireAboveOrSelf(sql, caller, { kind: 'goal', id: responsible.id });
  if (responsible.kind === 'manager')
    return requireInChain(sql, caller, { kind: 'manager', id: responsible.id });
  if (caller.kind === 'ticket') {
    if (
      sql.get<{
        manager_kind: string;
      }>('SELECT manager_kind FROM tickets WHERE id=?', [caller.id])?.manager_kind === 'chief'
    )
      return;
    throw new PlannerError('agent_forbidden', 'source belongs to another assignment');
  }
  requireAbove(sql, caller, { kind: 'owner_only', id: 'measure_sources' });
}
export function admitCommand(
  sql: AuthoritySql,
  caller: Principal,
  method: string,
  route: string,
  params: Record<string, string>,
  body: Record<string, unknown>,
  deps: AdmissionDependencies = {},
): void {
  const parts = route.replace(/^\/+|\/+$/g, '').split('/'),
    family = parts[0] === 'api' ? parts[1] : parts[0];
  const writing = !['GET', 'HEAD', 'OPTIONS'].includes(method);
  let target: Target | undefined,
    own = false;
  if (family === 'messages' && writing) return admitMessage(sql, caller, principal(body.target));
  if (family === 'tickets') {
    const id = params.ticket_id;
    if (!id) {
      if (method === 'POST') {
        let parent: string | null = null;
        if (body.manager !== undefined && body.manager !== null) {
          const manager = principal(body.manager);
          if (manager.kind === 'manager') {
            parent = manager.id;
            requireInChain(sql, caller, { kind: 'manager', id: parent });
          }
        }
        if (body.ceiling != null || body.ceiling_holder != null) {
          const created: Target = { kind: 'ticket', id: 'new-ticket' };
          if (
            !standsAbove(caller, created, {
              targetParentManagerId: parent,
              targetExists: true,
              callerParentManagerId: null,
            })
          )
            refuse(caller, created);
        }
      }
      return;
    }
    if (!writing || route.endsWith('/propose')) return;
    if (route.endsWith('/request-help')) {
      requireSelf(sql, caller, { kind: 'ticket', id });
      if (body.recipient != null) admitMessage(sql, caller, principal(body.recipient));
      return;
    }
    target = { kind: 'ticket', id };
    own =
      method === 'PATCH' &&
      !Object.keys(body).some((key) =>
        [
          'title',
          'project_id',
          'manager_id',
          'manager',
          'field_values',
          'ceiling',
          'ceiling_holder',
          'project',
        ].includes(key),
      );
    if (method === 'PATCH' && ('manager' in body || 'manager_id' in body))
      refuseManagerReparenting(caller, id);
  } else if (family === 'collections') {
    if (['manager_tickets', 'outcome_tickets'].includes(params.collection!)) {
      target = { kind: 'ticket', id: params.member_id! };
      refuseManagerReparenting(caller, target.id);
    } else if (params.collection === 'blockers') {
      target = { kind: 'ticket', id: params.container_id! };
      own = true;
    }
  } else if (family === 'managers' || family === 'goals') {
    const id = params[family === 'managers' ? 'item_id' : 'goal_id'];
    if (id) {
      if (route.endsWith('/settled')) return;
      if (writing || protectedSuffix(route)) {
        target = { kind: family === 'managers' ? 'manager' : 'goal', id };
        own = method === 'GET' || method === 'PATCH';
      }
    } else if (family === 'goals' && writing) target = { kind: 'owner_only', id: 'goals' };
  } else if (['artifact-catalog', 'conversation-history', 'files'].includes(family!)) {
    const kind =
      family === 'files'
        ? 'manager_id' in params
          ? 'manager'
          : 'goal_id' in params
            ? 'goal'
            : 'ticket'
        : params.kind;
    const id = params.owner_id ?? params.manager_id ?? params.goal_id;
    if ((kind === 'manager' || kind === 'goal') && id) {
      target = { kind, id };
      own = true;
    }
  } else if (family === 'conversation' && route.endsWith('/backends') && body.ticket_id) {
    target = { kind: 'ticket', id: String(body.ticket_id) };
    own = true;
  } else if (family === 'chief' && writing)
    target = { kind: 'owner_only', id: 'chief conversation' };
  else if (['projects', 'skills', 'workers', 'worker-types'].includes(family!) && writing)
    target = {
      kind: 'owner_only',
      id:
        family === 'workers'
          ? 'worker_settings'
          : family === 'worker-types'
            ? 'worker_types'
            : family!,
    };
  else if (family === 'credentials' && method === 'DELETE')
    target = { kind: 'owner_only', id: 'credentials' };
  else if (
    ['measure-sources', 'measures', 'datasets'].includes(family!) &&
    writing &&
    !route.endsWith('/read')
  ) {
    let source: string | undefined = params.source_id;
    if (family === 'measure-sources') {
      if (route.endsWith('/responsible')) target = { kind: 'owner_only', id: 'measure_sources' };
      else if (!source)
        return admitSource(
          sql,
          caller,
          required(deps.parseSourceResponsible, 'parseSourceResponsible')(body.responsible),
        );
    } else {
      const id = params[family === 'measures' ? 'measure_id' : 'dataset_id'];
      if (route.endsWith('/manual-writes')) {
        if (caller.kind === 'goal') return;
        if (
          caller.kind === 'manager' &&
          id &&
          required(deps.managerManualMeasure, 'managerManualMeasure')(sql, caller.id, id)
        )
          return;
        target = { kind: 'owner_only', id: 'measures' };
      } else if (id)
        source =
          required(deps.measureSource, 'measureSource')(
            sql,
            family as 'measures' | 'datasets',
            id,
          ) ?? undefined;
      else source = body.source_id as string | undefined;
      if (source == null && !target) target = { kind: 'owner_only', id: 'measures' };
    }
    if (!target && source != null)
      return admitSource(
        sql,
        caller,
        required(deps.sourceResponsible, 'sourceResponsible')(sql, source),
      );
  }
  if (target) (own ? requireAboveOrSelf : requireAbove)(sql, caller, target);
}
function protectedSuffix(route: string): boolean {
  return [
    '/launch-defaults',
    '/workspace',
    '/workspace-content',
    '/conversation/start-values',
    '/page',
    '/page/readings',
  ].some((suffix) => route.endsWith(suffix));
}
export function requestAdmission(
  sql: AuthoritySql,
  request: FastifyRequest,
  deps: AdmissionDependencies = {},
): void {
  const route = request.url.split('?')[0]!,
    params = object(request.params) as Record<string, string>;
  if (['GET', 'HEAD', 'OPTIONS'].includes(request.method)) {
    const protectedRead =
      ['/api/resource-body', '/api/resource-versions'].includes(route) ||
      ((route.includes('/managers/') || route.includes('/goals/')) && protectedSuffix(route)) ||
      (route.startsWith('/files/') && ('manager_id' in params || 'goal_id' in params)) ||
      ['manager', 'goal'].includes(params.kind ?? '');
    if (!protectedRead) return;
  }
  if (route === '/api/measures/read' || route === '/api/datasets/read') return;
  const caller = requestContext(request).principal;
  if (route === '/api/resource-body' || route === '/api/resource-versions') {
    const query = object(request.query);
    let keys: unknown;
    try {
      keys = route.endsWith('resource-body')
        ? [query.key ?? '']
        : JSON.parse(String(query.keys ?? '[]'));
    } catch {
      return;
    }
    if (Array.isArray(keys))
      for (const key of keys) {
        const match =
          typeof key === 'string'
            ? /^\/api\/managers\/([^/?]+)\/workspace-content$/.exec(key)
            : null;
        if (match)
          requireAboveOrSelf(sql, caller, { kind: 'manager', id: decodeURIComponent(match[1]!) });
      }
    return;
  }
  const media = (request.headers['content-type'] ?? '').split(';')[0]!.toLowerCase();
  const body =
    (!media || media === 'application/json' || /^application\/.+\+json$/.test(media)) &&
    !route.startsWith('/files/')
      ? object(request.body)
      : {};
  admitCommand(sql, caller, request.method, route, params, body, deps);
}
