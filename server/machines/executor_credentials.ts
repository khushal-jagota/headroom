/** Credential authority runs within the caller's serialized database write. */
import { createHash, randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID, valid_uuid } from '../execution_identity.ts';
import type { MachineCredentialGrant } from './contracts.ts';
import { validated_machine_name } from './data.ts';

function forbidden(): PlannerError {
  return new PlannerError('agent_forbidden', 'Executor credential is required or invalid');
}
function digest(credential: string): string {
  return createHash('sha256').update(credential).digest('hex');
}
function matches(verifier: string | null, credential: string): boolean {
  const supplied = Buffer.from(digest(credential));
  const stored = Buffer.from(verifier ?? '');
  return stored.length === supplied.length && timingSafeEqual(stored, supplied);
}
function require_platform(value: unknown): asserts value is 'darwin' | 'linux' {
  if (value !== 'darwin' && value !== 'linux')
    throw new PlannerError('validation', 'Executor platform must be darwin or linux');
}

export function authenticate_executor(
  tx: TransactionSql,
  claimed_id: string,
  credential: string | null,
  configured_original_id: string | null,
  authenticated_incarnation: string | null = null,
  options: { authenticated_platform?: string | null } = {},
): string {
  const machine_id =
    configured_original_id && claimed_id === configured_original_id
      ? ORIGINAL_MAC_MACHINE_ID
      : claimed_id;
  const row = tx.get<{
    credential_sha256: string | null;
    legacy_closed_at_ms: number | null;
    platform: string;
  }>(
    'SELECT credential_sha256,legacy_closed_at_ms,platform FROM machine_executor_credentials WHERE machine_id=?',
    [machine_id],
  );
  if (!row || machine_id === HOME_MACHINE_ID) throw forbidden();
  if (credential !== null) {
    if (!matches(row.credential_sha256, credential)) throw forbidden();
    const platform = options.authenticated_platform ?? null;
    if (platform !== null) require_platform(platform);
    if (platform !== null && platform !== row.platform)
      tx.run(
        'UPDATE machine_executor_credentials SET platform=? WHERE machine_id=? AND credential_sha256=? AND platform<>?',
        [platform, machine_id, digest(credential), platform],
      );
    return machine_id;
  }
  if (
    machine_id === ORIGINAL_MAC_MACHINE_ID &&
    configured_original_id !== null &&
    claimed_id === configured_original_id &&
    row.legacy_closed_at_ms === null
  )
    return machine_id;
  throw forbidden();
}

/** The executor owner calls this only after the credentialed current-update report succeeds. */
export function close_original_handover(
  tx: TransactionSql,
  machine_id: string,
  credential: string,
  authenticated_incarnation: string | null,
): void {
  const row = tx.get<{
    credential_sha256: string | null;
    legacy_closed_at_ms: number | null;
  }>(
    'SELECT credential_sha256,legacy_closed_at_ms FROM machine_executor_credentials WHERE machine_id=?',
    [machine_id],
  );
  if (!row || !matches(row.credential_sha256, credential)) throw forbidden();
  if (row.legacy_closed_at_ms === null)
    tx.run(
      'UPDATE machine_executor_credentials SET legacy_closed_at_ms=?,pending_credential=NULL WHERE machine_id=?',
      [Date.now(), machine_id],
    );
}

function issue(
  tx: TransactionSql,
  machine_id: string,
  incarnation: string | null,
): MachineCredentialGrant {
  const issued_at_ms = Date.now();
  const credential = randomBytes(32).toString('base64url');
  tx.run(
    'UPDATE machine_executor_credentials SET credential_sha256=?,issued_at_ms=?,issued_incarnation=?,legacy_closed_at_ms=COALESCE(legacy_closed_at_ms,?),pending_credential=NULL WHERE machine_id=?',
    [digest(credential), issued_at_ms, incarnation, issued_at_ms, machine_id],
  );
  return { machine_id, credential, issued_at_ms };
}

export function issue_original_credential(
  tx: TransactionSql,
  incarnation: string,
): MachineCredentialGrant {
  if (!valid_uuid(incarnation)) throw new TypeError('incarnation must be UUID');
  const row = tx.get<{
    legacy_closed_at_ms: number | null;
    pending_credential: string | null;
    issued_at_ms: number | null;
  }>(
    'SELECT legacy_closed_at_ms,pending_credential,issued_at_ms FROM machine_executor_credentials WHERE machine_id=?',
    [ORIGINAL_MAC_MACHINE_ID],
  );
  if (!row || row.legacy_closed_at_ms !== null) throw forbidden();
  if (row.pending_credential !== null)
    return {
      machine_id: ORIGINAL_MAC_MACHINE_ID,
      credential: row.pending_credential,
      issued_at_ms: row.issued_at_ms!,
    };
  const issued_at_ms = Date.now();
  const credential = randomBytes(32).toString('base64url');
  tx.run(
    'UPDATE machine_executor_credentials SET credential_sha256=?,issued_at_ms=?,issued_incarnation=?,pending_credential=? WHERE machine_id=?',
    [digest(credential), issued_at_ms, incarnation, credential, ORIGINAL_MAC_MACHINE_ID],
  );
  return { machine_id: ORIGINAL_MAC_MACHINE_ID, credential, issued_at_ms };
}

export function register_machine(
  tx: TransactionSql,
  name: string,
  platform: unknown = 'darwin',
): MachineCredentialGrant {
  require_platform(platform);
  const display_name = validated_machine_name(name);
  const machine_id = randomUUID();
  tx.run(
    "INSERT INTO machines(machine_id,name,icon,color,is_home) VALUES(?,?,'laptop','orchid',0)",
    [machine_id, display_name],
  );
  tx.run('INSERT INTO machine_executor_credentials(machine_id,platform) VALUES(?,?)', [
    machine_id,
    platform,
  ]);
  return issue(tx, machine_id, null);
}

export function replace_credential(tx: TransactionSql, machine_id: string): MachineCredentialGrant {
  if (
    machine_id === HOME_MACHINE_ID ||
    !tx.get('SELECT 1 FROM machine_executor_credentials WHERE machine_id=?', [machine_id])
  )
    throw new PlannerError('validation', 'Machine has no executor credential');
  return issue(tx, machine_id, null);
}

export function executor_platform(sql: ReadSql, machine_id: string): string {
  const row = sql.get<{ platform: string }>(
    'SELECT platform FROM machine_executor_credentials WHERE machine_id=?',
    [machine_id],
  );
  if (!row) throw new PlannerError('validation', 'Machine has no executor credential');
  return row.platform;
}
