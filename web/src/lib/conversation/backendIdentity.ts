/** The one line a backend card says about its login, and when it says nothing.
 *
 * Three identity statuses carry four different situations, and the difference that
 * matters here is between a login that needs replacing and one that merely cannot be
 * confirmed. A signed-out backend is told to sign in, because that is the only thing that
 * will fix it. A login whose saved token has expired is usually refreshed by the CLI
 * without anyone doing anything, so the card says what is known and offers no action: a
 * sign-in button there would send somebody to replace a credential that still works.
 */

import type { BackendSnapshot } from "./wire";

export type BackendIdentityNote = {
  text: string;
  /** Shown only where signing in is actually the remedy. */
  loginCommand: string | null;
};

export function backendIdentityNote(snapshot: BackendSnapshot): BackendIdentityNote | null {
  const identity = snapshot.identity;
  if (!identity) return null;
  if (identity.status === "unauthenticated") {
    return {
      text: identity.detail ?? "not signed in",
      loginCommand: identity.login_command ?? null
    };
  }
  if (identity.status === "unknown" && identity.detail) {
    return { text: identity.detail, loginCommand: null };
  }
  return null;
}
