/** Machine identity changes never move a Ticket or change executor authority. */
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { HOME_MACHINE_ID, legacy_machine_id } from '../execution_identity.ts';
import type { TicketLocale } from '../tickets/contracts.ts';
import { MACHINE_ICONS, MACHINE_COLORS, type Machine } from './contracts.ts';

export function read_machine(sql: ReadSql, machine_id: string): Machine {
  const row = sql.get<Omit<Machine, 'is_home'> & { is_home: number }>(
    'SELECT * FROM machines WHERE machine_id=?',
    [machine_id],
  );
  if (!row) throw new PlannerError('validation', 'Unknown machine destination', { machine_id });
  return { ...row, is_home: Boolean(row.is_home) };
}

export function list_machines(sql: ReadSql): Machine[] {
  return sql
    .all<{ machine_id: string }>(
      'SELECT machine_id FROM machines ORDER BY is_home DESC,name,machine_id',
    )
    .map(({ machine_id }) => read_machine(sql, machine_id));
}

export function resolve_destination(
  sql: ReadSql,
  machine_id: string | null,
  locale: TicketLocale | null,
): { machine_id: string; locale: TicketLocale } {
  const selected = machine_id ?? legacy_machine_id(locale ?? 'vps');
  read_machine(sql, selected);
  if (locale !== null && selected !== legacy_machine_id(locale))
    throw new PlannerError('validation', 'machine_id and locale name different destinations');
  return { machine_id: selected, locale: selected === HOME_MACHINE_ID ? 'vps' : 'macos' };
}

export function edit_machine(
  tx: TransactionSql,
  machine_id: string,
  body: Record<string, unknown>,
): Machine {
  const old = read_machine(tx, machine_id);
  if (Object.keys(body).some((key) => !['name', 'icon', 'color'].includes(key)))
    throw new PlannerError('validation', 'Only machine name, icon, and color are editable');
  const name = Object.hasOwn(body, 'name') ? body.name : old.name;
  const icon = Object.hasOwn(body, 'icon') ? body.icon : old.icon;
  const color = Object.hasOwn(body, 'color') ? body.color : old.color;
  const display_name = validated_machine_name(name);
  if (
    !MACHINE_ICONS.some((value) => value === icon) ||
    !MACHINE_COLORS.some((value) => value === color)
  )
    throw new PlannerError('validation', 'Unknown machine icon or color');
  tx.run('UPDATE machines SET name=?,icon=?,color=? WHERE machine_id=?', [
    display_name,
    icon as string,
    color as string,
    machine_id,
  ]);
  return read_machine(tx, machine_id);
}

export function validated_machine_name(value: unknown): string {
  // Match the stored label from its first through its last non-whitespace character.
  const name =
    typeof value === 'string'
      ? (value.match(
          /[^\p{White_Space}\u001c-\u001f](?:[\s\S]*[^\p{White_Space}\u001c-\u001f])?/u,
        )?.[0] ?? '')
      : '';
  if (!name || Array.from(name).length > 80)
    throw new PlannerError('validation', 'Machine name must contain 1 to 80 characters');
  return name;
}
