/** What a backend card offers when a login needs replacing, and what it warns about first.
 *
 * Two rules are worth holding onto here. A sign-in is only offered where Panels can
 * actually run one, so every other backend keeps the command-line text rather than a
 * button that cannot work. And the retry caveat is shown to somebody who has something to
 * lose — before they start, not after it worked.
 */

import { describe, expect, it } from "vitest";

import {
  backendCanBeSignedIn,
  signInCaveat,
  signInIsPending,
  signInOutcome
} from "../src/lib/conversation/backendSignIn";
import type { BackendSignIn, BackendSnapshot } from "../src/lib/conversation/wire";

function snapshot(
  overrides: Partial<BackendSnapshot> & { identity?: BackendSnapshot["identity"] } = {}
): BackendSnapshot {
  return {
    backend_key: "codex",
    installed: true,
    executable_path: "/usr/bin/codex",
    version: "0.163.0",
    identity: {
      status: "unauthenticated",
      account_label: null,
      detail: null,
      login_command: "codex login"
    },
    available_models: [],
    reasoning_effort_options: [],
    default_model_id: null,
    default_reasoning_effort: null,
    update_advisory: null,
    diagnoses: [],
    cached_usage: null,
    ...overrides
  } as unknown as BackendSnapshot;
}

function signIn(overrides: Partial<BackendSignIn> = {}): BackendSignIn {
  return {
    session_id: "s1",
    state: "pending",
    verification_url: "https://auth.openai.com/codex/device",
    user_code: "ABCD-1234",
    started_at: "2026-10-16T00:00:00+00:00",
    deadline_at: "2026-10-16T00:10:00+00:00",
    detail: null,
    account_label: null,
    ...overrides
  };
}

describe("backendCanBeSignedIn", () => {
  it("offers a sign-in for an installed codex with an account", () => {
    expect(backendCanBeSignedIn(snapshot())).toBe(true);
  });

  it("offers nothing for claude, which Panels cannot sign in", () => {
    expect(backendCanBeSignedIn(snapshot({ backend_key: "claude" }))).toBe(false);
  });

  it("offers nothing for a codex that is not installed", () => {
    expect(backendCanBeSignedIn(snapshot({ installed: false }))).toBe(false);
  });

  it("offers nothing for a backend with no account to sign in to", () => {
    expect(backendCanBeSignedIn(snapshot({ identity: null }))).toBe(false);
  });
});

describe("signInCaveat", () => {
  it("warns a usable account that a sign-in writes a new credential", () => {
    const caveat = signInCaveat(
      snapshot({
        identity: {
          status: "authenticated",
          account_label: "owner@example.com",
          detail: null,
          login_command: "codex login"
        }
      })
    );

    expect(caveat).toContain("new credential");
    expect(caveat).toContain("retried");
  });

  it("warns a login nobody has tested, because it may still work", () => {
    expect(
      signInCaveat(
        snapshot({
          identity: {
            status: "unknown",
            account_label: null,
            detail: "expired and not refreshed yet",
            login_command: "codex login"
          }
        })
      )
    ).not.toBeNull();
  });

  it("says nothing to an account that already cannot authenticate", () => {
    // Nothing to lose, and a caveat here would be noise in front of the one action that
    // fixes it.
    expect(signInCaveat(snapshot())).toBeNull();
  });

  it("says nothing for a backend that cannot be signed in at all", () => {
    expect(signInCaveat(snapshot({ backend_key: "claude" }))).toBeNull();
  });
});

describe("signInIsPending", () => {
  it("is true only while a sign-in is still running", () => {
    expect(signInIsPending(signIn())).toBe(true);
    expect(signInIsPending(signIn({ state: "ended" }))).toBe(false);
    expect(signInIsPending(null)).toBe(false);
  });
});

describe("signInOutcome", () => {
  it("says nothing while a sign-in is pending", () => {
    expect(signInOutcome(signIn())).toBeNull();
  });

  it("names the account a confirmed sign-in landed on", () => {
    expect(
      signInOutcome(
        signIn({ state: "confirmed", account_label: "owner@example.com", user_code: null })
      )
    ).toBe("Signed in as owner@example.com.");
  });

  it("reports an unconfirmed sign-in as neither signed in nor failed", () => {
    const outcome = signInOutcome(
      signIn({
        state: "unconfirmed",
        detail: "Codex reported a sign-in, but the saved login still holds the credential it had before."
      })
    );

    expect(outcome).toContain("still holds the credential");
    expect(outcome).not.toContain("Signed in");
  });

  it("falls back to plain words when an unconfirmed sign-in carries no detail", () => {
    expect(signInOutcome(signIn({ state: "unconfirmed" }))).toContain("could not confirm");
  });

  it("reports an ended sign-in with the reason it was given", () => {
    expect(
      signInOutcome({
        ...signIn({ state: "ended" }),
        detail: "Panels stopped showing the code after its own time limit."
      })
    ).toBe("Panels stopped showing the code after its own time limit.");
  });
});

describe("signInCaveat with turns in flight", () => {
  it("warns an unauthenticated card when a turn is running against it", () => {
    // The approved condition is a usable account OR turns in flight. A dead-looking
    // account with a running turn still has a turn that may have to be retried.
    const caveat = signInCaveat(snapshot(), { turnsInFlight: true });

    expect(caveat).toContain("new credential");
    expect(caveat).toContain("retried");
  });

  it("still says nothing for an unauthenticated card with nothing running", () => {
    expect(signInCaveat(snapshot(), { turnsInFlight: false })).toBeNull();
  });

  it("warns a usable account whether or not a turn is running", () => {
    const usable = snapshot({
      identity: {
        status: "authenticated",
        account_label: "owner@example.com",
        detail: null,
        login_command: "codex login"
      }
    });

    expect(signInCaveat(usable, { turnsInFlight: false })).not.toBeNull();
    expect(signInCaveat(usable, { turnsInFlight: true })).not.toBeNull();
  });

  it("offers no caveat for a backend that cannot be signed in, running turn or not", () => {
    expect(signInCaveat(snapshot({ backend_key: "claude" }), { turnsInFlight: true })).toBeNull();
  });
});
