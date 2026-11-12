import { CHIEF, OWNER, type Principal } from '../../core/contracts.ts';
import type { WorkflowDefinition } from '../../worker_types/contracts.ts';
import { at_or_beyond_ceiling } from './machine.ts';
export interface ProposalRoute {
    kind: 'auto_accept' | 'item_manager' | 'chief' | 'owner';
    manager_id: string | null;
}
export function route_to_holder(holder: Principal): ProposalRoute { return holder.kind === 'chief' ? { kind: 'chief', manager_id: null } : holder.kind === 'manager' ? { kind: 'item_manager', manager_id: holder.id } : { kind: 'owner', manager_id: null }; }
export function route_proposal(options: {
    stage: string;
    ceiling: string;
    holder: Principal;
    worker_type_definition: WorkflowDefinition;
}): ProposalRoute { return at_or_beyond_ceiling(options.stage, options.ceiling, options) ? route_to_holder(options.holder) : { kind: 'auto_accept', manager_id: null }; }
export function parked_destination(route: ProposalRoute): Principal | null { return route.kind === 'auto_accept' ? null : route.kind === 'item_manager' ? { kind: 'manager', id: route.manager_id! } : route.kind === 'chief' ? CHIEF : OWNER; }
