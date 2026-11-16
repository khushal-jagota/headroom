import { failure_name } from '../failure_name.ts';
/** Diagnostic fields only. Native/provider payload messages stay outside retry logs. */
export function mailbox_failure(error: unknown): string {
  const fields = error as { status?: unknown; code?: unknown } | null;
  return (
    failure_name(error) +
    (typeof fields?.status === 'number' ? ` status=${fields.status}` : '') +
    (typeof fields?.code === 'string' ? ` code=${fields.code}` : '')
  );
}
