/** Machine settings and one-time credential grants use the ordinary owner boundary. */
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { requireOwner } from '../core/authority/identity.ts';
import { PlannerError } from '../core/contracts.ts';
import { is_record } from '../core/records.ts';
import { JsonNumber } from '../core/http/numeric_json.ts';
import { HOME_MACHINE_ID } from '../execution_identity.ts';
import { now_ms } from '../executor/clock.ts';
import { mac_is_present } from '../tickets/locale_configuration.ts';
import { add_work_attention } from '../work_attention.ts';
import type { MachineView } from './contracts.ts';
import { edit_machine, list_machines } from './data.ts';
import { register_machine, replace_credential } from './executor_credentials.ts';

async function machine_views(services: AreaServices): Promise<{ machines: MachineView[] }> {
  const { records, present, tickets } = services.database.read((sql) => {
    const records = list_machines(sql);
    const present = new Set(records.filter((machine) =>
      machine.is_home || mac_is_present(sql, now_ms(), machine.machine_id),
    ).map((machine) => machine.machine_id));
    const tickets = sql.all<Record<string, unknown>>(
      "SELECT t.id,t.machine_id,json_extract(n.value,'$.ticket_status') AS ticket_status FROM tickets t JOIN navigation_records n ON n.kind='ticket' AND n.id=t.id",
    );
    return { records, present, tickets };
  });
  await add_work_attention(services.database, services.conversationSystem, tickets);
  const by_destination = new Map<string, Record<string, unknown>[]>();
  for (const ticket of tickets) {
    const machine_id = String(ticket.machine_id);
    const rows = by_destination.get(machine_id) ?? [];
    rows.push(ticket);
    by_destination.set(machine_id, rows);
  }
  return {
    machines: records.map((machine) => {
      const available = machine.machine_id === HOME_MACHINE_ID || present.has(machine.machine_id);
      const rows = by_destination.get(machine.machine_id) ?? [];
      const running = rows.filter((row) =>
        Boolean((row.conversation_signal as { working?: boolean } | undefined)?.working),
      ).length;
      return {
        ...machine,
        available,
        running_workers: available ? running : null,
        waiting_tickets: available ? 0 : rows.filter((row) =>
          row.ticket_status === 'agent' || row.agent_state === 'working',
        ).length,
      };
    }),
  };
}

export const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  app.get('/machines', () => machine_views(services));
  app.patch<{ Params: { machine_id: string } }>('/machines/:machine_id', async (request) => {
    requireOwner(services.identity(request));
    const body = request.body;
    if (!is_record(body) || body instanceof JsonNumber)
      throw new PlannerError('validation', 'Machine settings must be an object');
    services.database.write((tx) => edit_machine(tx, request.params.machine_id, body));
    const response = await machine_views(services);
    return response.machines.find((machine) => machine.machine_id === request.params.machine_id)!;
  });
  app.post('/machines', (request) => {
    requireOwner(services.identity(request));
    const body = request.body;
    if (!is_record(body) || body instanceof JsonNumber ||
        Object.keys(body).some((key) => key !== 'name' && key !== 'platform') ||
        typeof body.name !== 'string')
      throw new PlannerError('validation', 'Machine registration requires a name');
    return services.database.write((tx) => register_machine(tx, body.name as string,
      Object.hasOwn(body, 'platform') ? body.platform : 'darwin'));
  });
  app.post<{ Params: { machine_id: string } }>('/machines/:machine_id/credential', (request) => {
    requireOwner(services.identity(request));
    return services.database.write((tx) => replace_credential(tx, request.params.machine_id));
  });
};
