import { createHash } from 'node:crypto';
import type { FastifyRequest } from 'fastify';
import type { ReadSql } from '../../core/database/contracts.ts';
import type { ResourceProvider, ResourceSnapshot } from '../../resource_versions/contracts.ts';
import { HOME_MACHINE_ID } from '../../execution_identity.ts';
import { read_ticket } from '../../tickets/data/index.ts';
import { read_machine } from '../../machines/data.ts';
import { read_presence } from '../../executor/vps_presence_store.ts';
import { judge_presence } from '../../executor/logic/presence.ts';
import {
  MAC_AWAY_DIAGNOSIS,
  empty_machine_snapshot,
  decode_machine_snapshot,
} from '../../executor/catalog_relay.ts';
import { backend_card_state, capability_source } from '../backend_state.ts';
import { snapshot_json } from './presentation.ts';
import type { BackendHttpRuntime, BackendSnapshot } from './contracts.ts';

export function backend_resource_key(
  selection: { machine_id: string } | { ticket_id: string },
): string {
  if ('ticket_id' in selection)
    return `/api/conversation/backends?ticket_id=${encodeURIComponent(selection.ticket_id)}`;
  return (
    '/api/conversation/backends' +
    (selection.machine_id === HOME_MACHINE_ID
      ? ''
      : `?machine_id=${encodeURIComponent(selection.machine_id)}`)
  );
}
/** Saved facts only. Cold hydration stays in the asynchronous backend HTTP routes. */
export class BackendPickerResources implements ResourceProvider {
  private readonly runtime: BackendHttpRuntime;
  constructor(runtime: BackendHttpRuntime) {
    this.runtime = runtime;
  }
  capture(sql: ReadSql, _request: FastifyRequest, key: string): ResourceSnapshot | null {
    const url = new URL(key, 'http://panels.invalid');
    if (url.pathname !== '/api/conversation/backends') return null;
    if (
      [...url.searchParams.keys()].some((name) => !['ticket_id', 'machine_id'].includes(name)) ||
      url.searchParams.size > 1
    )
      return null;
    const ticket_id = url.searchParams.get('ticket_id');
    const machine_id =
      ticket_id === null
        ? (url.searchParams.get('machine_id') ?? HOME_MACHINE_ID)
        : read_ticket(sql, ticket_id).machine_id;
    read_machine(sql, machine_id);
    const present =
      judge_presence(read_presence(sql, machine_id), Date.now()).presence === 'present';
    const state = backend_card_state(sql);
    const sources: string[] = [];
    const backends = this.runtime.backend_ids.map((backend_key) => {
      const row = sql.get<{ snapshot_json: string }>(
        'SELECT snapshot_json FROM machine_backend_snapshots WHERE machine_id=? AND backend_key=?',
        [machine_id, backend_key],
      );
      // A failed cold discovery remains retryable through the ordinary backend route.
      const snapshot: BackendSnapshot | null = row
        ? decode_machine_snapshot(row.snapshot_json)
        : this.runtime.machine_backends === undefined
          ? this.runtime.backend_snapshots.retained(backend_key)
          : null;
      sources.push(capability_source(sql, machine_id, backend_key));
      const saved = snapshot ?? empty_machine_snapshot(backend_key);
      const capabilities = sql.get<{ capabilities_json: string | null }>(
        'SELECT capabilities_json FROM machine_backend_state WHERE machine_id=? AND backend_key=?',
        [machine_id, backend_key],
      );
      const facts =
        this.runtime.machine_backends === undefined
          ? {
              can_sign_in: this.runtime.sign_in(backend_key) !== null,
              reports_allowance_usage: this.runtime.reports_allowance_usage(backend_key),
            }
          : capabilities?.capabilities_json
            ? JSON.parse(capabilities.capabilities_json)
            : { can_sign_in: false, reports_allowance_usage: false };
      const answer =
        this.runtime.machine_backends !== undefined && !present
          ? { ...saved, diagnoses: [...saved.diagnoses, MAC_AWAY_DIAGNOSIS] }
          : saved;
      return snapshot_json(
        answer,
        state,
        { driver: this.runtime.driver_of(backend_key), ...facts },
        machine_id,
      );
    });
    const body = { backends };
    const revision = createHash('sha256')
      .update(JSON.stringify([machine_id, present, sources, body]))
      .digest('hex');
    return { revision, read: () => body };
  }
}
