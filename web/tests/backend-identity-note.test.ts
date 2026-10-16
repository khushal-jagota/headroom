/** What a backend card says about its login.
 *
 * The card used to say one of two things: nothing, or "not signed in". A saved login that
 * has expired and has not been refreshed is neither — it is a login nobody has tested —
 * and calling it good is what let a dead codex account look healthy for twelve days. The
 * rule this keeps is that the card states what is known and only offers a sign-in where
 * signing in is the remedy.
 */

import { describe, expect, it } from "vitest";

import { backendIdentityNote } from "../src/lib/conversation/backendIdentity";
import type { BackendSnapshot } from "../src/lib/conversation/wire";

function snapshot(identity: BackendSnapshot["identity"]): BackendSnapshot {
  return {
    backend_key: "codex",
    installed: true,
    executable_path: "/usr/bin/codex",
    version: "0.145.0",
    identity,
    available_models: [],
    reasoning_effort_options: [],
    default_model_id: null,
    default_reasoning_effort: null,
    update_advisory: null,
    diagnoses: [],
    cached_usage: null
  } as unknown as BackendSnapshot;
}

describe("backendIdentityNote", () => {
  it("says nothing for a login that is confirmed good", () => {
    expect(
      backendIdentityNote(
        snapshot({
          status: "authenticated",
          account_label: "someone@example.com",
          detail: null,
          login_command: "codex login"
        })
      )
    ).toBeNull();
  });

  it("offers the sign-in command when signing in is the remedy", () => {
    const note = backendIdentityNote(
      snapshot({
        status: "unauthenticated",
        account_label: null,
        detail: null,
        login_command: "codex login"
      })
    );

    expect(note?.text).toBe("not signed in");
    expect(note?.loginCommand).toBe("codex login");
  });

  it("states a rejected login's own reason, and still offers the sign-in", () => {
    const note = backendIdentityNote(
      snapshot({
        status: "unauthenticated",
        account_label: "someone@example.com",
        detail: "This saved login was rejected the last time codex used it.",
        login_command: "codex login"
      })
    );

    expect(note?.text).toBe("This saved login was rejected the last time codex used it.");
    expect(note?.loginCommand).toBe("codex login");
  });

  it("states an unconfirmed login's reason and offers no action", () => {
    const note = backendIdentityNote(
      snapshot({
        status: "unknown",
        account_label: "someone@example.com",
        detail: "This saved login expired and codex has not refreshed it yet.",
        login_command: "codex login"
      })
    );

    expect(note?.text).toBe("This saved login expired and codex has not refreshed it yet.");
    expect(note?.loginCommand).toBeNull();
  });

  it("says nothing when the probe had nothing to add", () => {
    expect(
      backendIdentityNote(
        snapshot({
          status: "unknown",
          account_label: null,
          detail: null,
          login_command: "codex login"
        })
      )
    ).toBeNull();
  });
});
