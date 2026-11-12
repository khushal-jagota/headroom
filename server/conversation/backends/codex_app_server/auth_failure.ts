/** Read only what a failed turn establishes about its own Codex login. */
export const CodexAuthReading = {
  login_is_rejected: 'login_is_rejected',
  authorization_was_refused: 'authorization_was_refused',
  says_nothing_about_the_login: 'says_nothing_about_the_login',
} as const;
export type CodexAuthReading = (typeof CodexAuthReading)[keyof typeof CodexAuthReading];

const CODEX_AUTH_SPEAKER = /(?<![\p{L}\p{N}_])codex_login::[\p{L}\p{N}_:]*auth/u;
const LOGIN_IS_REJECTED_PHRASES = [
  'refresh token was already used',
  'log out and sign in again',
  'not logged in',
  'you are logged out',
  'invalid_grant',
];
const AUTHORIZATION_WAS_REFUSED =
  /unauthorized|forbidden|authentication failed|(?<![\p{L}\p{N}_])(?:401|403)(?![\p{L}\p{N}_])/u;
export const LOGIN_IS_REJECTED_SUMMARY =
  'Codex is signed out: it has a saved login and the server rejected it. ' +
  'Run `codex login` on this machine, then send this again.';

export function read_a_failed_turn(options: {
  error_summary: string | null;
  standard_error_this_turn: string | null;
}): CodexAuthReading {
  const codex_said = (options.error_summary ?? '').toLowerCase();
  const about_the_login = [
    codex_said,
    ...(options.standard_error_this_turn ?? '')
      .split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/)
      .filter((line) => CODEX_AUTH_SPEAKER.test(line))
      .map((line) => line.toLowerCase()),
  ].join('\n');
  if (LOGIN_IS_REJECTED_PHRASES.some((phrase) => about_the_login.includes(phrase)))
    return CodexAuthReading.login_is_rejected;
  if (AUTHORIZATION_WAS_REFUSED.test(codex_said))
    return CodexAuthReading.authorization_was_refused;
  return CodexAuthReading.says_nothing_about_the_login;
}

export function error_summary_for(
  reading: CodexAuthReading,
  error_summary: string | null,
): string | null {
  if (reading === CodexAuthReading.login_is_rejected) return LOGIN_IS_REJECTED_SUMMARY;
  if (reading === CodexAuthReading.authorization_was_refused) {
    const refused = 'the request was refused; the Codex sign-in may need attention';
    return error_summary
      ? `${error_summary} — ${refused}`
      : refused[0]!.toUpperCase() + refused.slice(1);
  }
  return error_summary;
}
