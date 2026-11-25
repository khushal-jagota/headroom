/** Only the owner can relocate work. Ordinary agent authority does not grant this action. */
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { requireOwner } from '../core/authority/identity.ts';
import { PlannerError } from '../core/contracts.ts';
import { is_record } from '../core/records.ts';
import type { WorkMoveOwner, WorkMoveRequest } from './contracts.ts';
import { work_move_status } from './data.ts';
import { move_work, move_everything } from './service.ts';
import { create_operation, read_operations, work_move_runtime } from './operations.ts';

function machine_input(input: unknown): string {
  if (!is_record(input) || typeof input.machine_id !== 'string' || !input.machine_id)
    throw new PlannerError('validation', 'Select a machine for the move.');
  return input.machine_id;
}
function request_input(input: unknown): WorkMoveRequest {
  const machine_id = machine_input(input);
  const body = input as Record<string, unknown>;
  if (
    Object.keys(body).some((key) => !['owner', 'machine_id'].includes(key)) ||
    !is_record(body.owner)
  )
    throw new PlannerError('validation', 'Name one Ticket, Manager or the Chief to move.');
  const owner = body.owner;
  if (owner.kind === 'chief' && Object.keys(owner).length === 1)
    return { owner: { kind: 'chief' }, machine_id };
  if (
    ['ticket', 'manager'].includes(String(owner.kind)) &&
    typeof owner.id === 'string' &&
    owner.id &&
    Object.keys(owner).length === 2
  )
    return { owner: owner as WorkMoveOwner, machine_id };
  throw new PlannerError('validation', 'Name one Ticket, Manager or the Chief to move.');
}
export const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  const runtime = work_move_runtime(services.database);
  app.addHook('preClose', async () => {
    runtime.seal();
  });
  app.addHook('onClose', async () => {
    await runtime.settled();
  });
  const move_services = () => {
    if (!services.conversationSystem || !app.executor_registry)
      throw new PlannerError('validation', 'Machine work is unavailable.');
    return {
      database: services.database,
      system: services.conversationSystem,
      registry: app.executor_registry,
      now: () => services.clock.nowUnix(),
    };
  };
  app.get('/work-moves/operations', (request) => {
    requireOwner(services.identity(request));
    return { operations: services.database.read(read_operations) };
  });
  app.post('/work-moves/operations', (request, reply) => {
    requireOwner(services.identity(request));
    runtime.assert_open();
    const machine_id = machine_input(request.body);
    const body = request.body as Record<string, unknown>;
    const owner = 'owner' in body ? request_input(body).owner : undefined;
    if (owner === undefined && Object.keys(body).length !== 1)
      throw new PlannerError('validation', 'Select one destination for Move everything.');
    const selected_services = move_services();
    const operation = create_operation(selected_services, machine_id, owner);
    runtime.submit(selected_services, operation);
    return reply.code(202).send(operation);
  });
  app.get('/work-moves', (request) => {
    requireOwner(services.identity(request));
    return services.database.read(work_move_status);
  });
  app.post('/work-moves', async (request) => {
    requireOwner(services.identity(request));
    return move_work(move_services(), request_input(request.body));
  });
  app.post('/work-moves/everything', async (request) => {
    requireOwner(services.identity(request));
    const machine_id = machine_input(request.body);
    if (Object.keys(request.body as object).length !== 1)
      throw new PlannerError('validation', 'Select one destination for Move everything.');
    return move_everything(move_services(), machine_id);
  });
};
