/** Whether a backend card offers a sign-in, and what it warns about before starting one.
 *
 * Two decisions live here rather than in the card's markup, because both of them are
 * easy to get subtly wrong and neither is about layout.
 *
 * The first is **whether to offer it at all**. Only codex can be signed in from Panels, so
 * every other backend keeps the command-line text it has always had. Offering a button
 * that cannot work would be worse than offering nothing.
 *
 * The second is **when to warn**. A sign-in writes a new credential, and a turn already
 * running may have to be retried. That is worth saying — but only to somebody who has
 * something to lose. An account that already cannot authenticate has nothing to lose, and
 * a caveat there would be noise in front of the one action that fixes it. The warning also
 * belongs *before* the sign-in starts: disclosure after the fact is not disclosure.
 */

import type { BackendSignIn, BackendSnapshot } from "./wire";

/** The one backend Panels can sign in. Everything else keeps the command-line text. */
export const SIGN_IN_CAPABLE_BACKEND = "codex";

export function backendCanBeSignedIn(snapshot: BackendSnapshot): boolean {
  return (
    snapshot.backend_key === SIGN_IN_CAPABLE_BACKEND &&
    snapshot.installed &&
    snapshot.identity !== null
  );
}

/**
 * The caveat to show before a sign-in starts, or null when there is nothing to lose.
 *
 * An `authenticated` card is a login Panels believes works. An `unknown` one is a login
 * nobody has tested, which may well still work — so both get the caveat. Only an
 * `unauthenticated` card is known to be unusable, and that one gets none.
 */
export function signInCaveat(snapshot: BackendSnapshot): string | null {
  if (!backendCanBeSignedIn(snapshot)) return null;
  if (snapshot.identity?.status === "unauthenticated") return null;
  return (
    "Signing in writes a new credential for this account. A turn already running may" +
    " have to be retried."
  );
}

/** Whether a sign-in is still running, and so whether the page should keep watching it. */
export function signInIsPending(signIn: BackendSignIn | null): boolean {
  return signIn !== null && signIn.state === "pending";
}

/**
 * The sentence a settled sign-in is reported with.
 *
 * `unconfirmed` is the one worth reading twice: codex reported success and Panels could
 * not see a usable new credential. It is neither signed in nor failed, and it says so.
 */
export function signInOutcome(signIn: BackendSignIn | null): string | null {
  if (signIn === null || signIn.state === "pending") return null;
  if (signIn.state === "confirmed") {
    return signIn.account_label === null
      ? "Signed in."
      : `Signed in as ${signIn.account_label}.`;
  }
  if (signIn.state === "unconfirmed") {
    return (
      signIn.detail ??
      "Codex reported a sign-in and Panels could not confirm a usable credential."
    );
  }
  return signIn.detail ?? "The sign-in ended.";
}
