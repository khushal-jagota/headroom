import type { FastifyRequest } from 'fastify';
import { CHIEF, OWNER, PlannerError, legacyActor } from '../contracts.ts';
import type { RequestContext } from '../contracts.ts';
function normalized(value: string | string[] | undefined): string | null {
  return (Array.isArray(value) ? value[0] : value)?.trim() || null;
}
function firstHeader(request: FastifyRequest, name: string): string | string[] | undefined {
  // Trusted ingress can remove untrusted claim headers before admission.
  if (!(name in request.headers)) return undefined;
  const raw = request.raw.rawHeaders;
  for (let i = 0; i < raw.length; i += 2) {
    if (raw[i]?.toLowerCase() === name) return raw[i + 1];
  }
  return request.headers[name];
}
export function requestContext(request: FastifyRequest): RequestContext {
  const actor = normalized(firstHeader(request, 'x-plan-actor'));
  const ticket = normalized(firstHeader(request, 'x-plan-ticket-id'));
  const manager = normalized(firstHeader(request, 'x-plan-manager-id'));
  const conversationId = normalized(firstHeader(request, 'x-plan-conversation-id'));
  if (actor === null) return { principal: OWNER, conversationId };
  if (actor === 'chief') return { principal: CHIEF, conversationId };
  if (actor === 'worker' && ticket)
    return { principal: { kind: 'ticket', id: ticket }, conversationId };
  if (actor === 'manager' && manager)
    return { principal: { kind: 'manager', id: manager }, conversationId };
  throw new PlannerError('agent_forbidden', 'request actor does not name a Panels principal', {
    actor,
  });
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
