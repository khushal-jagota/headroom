/** Identity readers interpret local CLI answers and the saved login without spending it. */
import type { BackendIdentity, CommandOutcome, CodexLoginStanding } from './contracts.ts';
function identity(
  status: BackendIdentity['status'],
  account_label: string | null = null,
  detail: string | null = null,
): BackendIdentity {
  return { status, account_label, detail, login_command: null };
}
const optional_text = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() ? value.trim() : null;
export function read_claude_identity(outcome: CommandOutcome): BackendIdentity {
  if (outcome.exit_code !== 0) return identity('unknown');
  let reported: unknown;
  try {
    reported = JSON.parse(outcome.standard_output);
  } catch {
    return identity('unknown');
  }
  if (reported === null || typeof reported !== 'object' || Array.isArray(reported))
    return identity('unknown');
  const value = reported as Record<string, unknown>;
  if (typeof value.loggedIn !== 'boolean') return identity('unknown');
  if (!value.loggedIn) return identity('unauthenticated');
  const subscription = optional_text(value.subscriptionType);
  const method = optional_text(value.authMethod);
  const detail =
    subscription !== null && method !== null
      ? `${subscription} plan via ${method}`
      : subscription || method;
  return identity('authenticated', optional_text(value.email), detail);
}
export function read_codex_identity(
  outcome: CommandOutcome,
  standing: CodexLoginStanding | null,
  now: Date,
): BackendIdentity {
  const reported = outcome.standard_output.trim() || outcome.standard_error.trim();
  const cli_line = reported ? reported.split(/[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/)[0] : null;
  if (outcome.exit_code !== 0 || reported.toLowerCase().includes('not logged in'))
    return identity('unauthenticated');
  if (standing === null)
    return reported ? identity('authenticated', cli_line) : identity('unknown');
  const stored = standing.stored;
  const label = stored.account_label || cli_line;
  if (!stored.present) return identity('unauthenticated');
  if (standing.rejected)
    return identity(
      'unauthenticated',
      label,
      'This saved login was rejected the last time codex used it.',
    );
  if (!stored.readable)
    return identity(
      'unknown',
      label,
      'Panels could not read the saved codex login, so it cannot say more.',
    );
  if (stored.access_token_has_expired({ now }))
    return identity(
      'unknown',
      label,
      'This saved login expired and codex has not refreshed it yet. Panels cannot confirm it works until a turn runs.',
    );
  if (!reported) return identity('unknown', label);
  return identity('authenticated', label);
}
