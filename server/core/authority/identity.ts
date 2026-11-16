import { AsyncLocalStorage } from 'node:async_hooks';
import type { FastifyRequest } from 'fastify';
import { OWNER, PlannerError, legacyActor } from '../contracts.ts';
import type { RequestContext } from '../contracts.ts';
import type { ReadSql } from '../database/contracts.ts';
import { authenticate_launch_token } from '../../agent_identity/data.ts';

const in_process_context = new AsyncLocalStorage<RequestContext>();
export function with_in_process_request_context<T>(context: RequestContext, work: () => T): T {
  return in_process_context.run(context, work);
}
export const TRUSTED_INGRESS_CONFIGURED = Symbol('trusted ingress configured');
type IdentityRequest = FastifyRequest & {
  [TRUSTED_INGRESS_CONFIGURED]?: boolean;
  authenticatedOwner?: boolean;
};
export function requestContext(request: FastifyRequest, sql?: ReadSql): RequestContext {
  const identityRequest = request as IdentityRequest;
  const internal = in_process_context.getStore();
  if (internal) return internal;
  const unidentified: RequestContext = {
    principal: { kind: 'unidentified', id: 'unidentified' },
    conversationId: null,
  };
  const authorizations: string[] = [];
  for (let index = 0; index < request.raw.rawHeaders.length; index += 2) {
    if (request.raw.rawHeaders[index]?.toLowerCase() === 'authorization')
      authorizations.push(request.raw.rawHeaders[index + 1]!);
  }
  if (authorizations.length > 1) return unidentified;
  const authorization = authorizations[0] ?? request.headers.authorization;
  if (authorization !== undefined) {
    const parts = authorization.trim().split(/\s+/);
    if (parts.length !== 2 || parts[0]!.toLowerCase() !== 'bearer' || !sql) return unidentified;
    const identity = authenticate_launch_token(sql, parts[1]!);
    return identity
      ? { principal: identity.principal, conversationId: identity.conversation_id }
      : unidentified;
  }
  if (identityRequest.authenticatedOwner === true)
    return { principal: OWNER, conversationId: null };
  const legacyClaim = Object.keys(request.headers).some((name) =>
    name.toLowerCase().startsWith('x-plan-'),
  );
  return !legacyClaim && !identityRequest[TRUSTED_INGRESS_CONFIGURED]
    ? { principal: OWNER, conversationId: null }
    : unidentified;
}
export function requireOwner(context: RequestContext): void {
  if (context.principal.kind !== 'owner')
    throw new PlannerError('agent_forbidden', 'this operation is available only to the owner', {
      actor: legacyActor(context.principal),
    });
}
export function requireOwnerOrChief(context: RequestContext): void {
  if (!['owner', 'chief'].includes(context.principal.kind))
    throw new PlannerError(
      'agent_forbidden',
      'this operation is available only to the owner or the Chief',
      { actor: legacyActor(context.principal) },
    );
}
