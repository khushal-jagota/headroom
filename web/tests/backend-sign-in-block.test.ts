/** Which sign-in state draws what on Settings → VPS backends.
 *
 * The risks are the paste box showing in the wrong state, and the block appearing for an
 * account that already works, which would change how a signed-in Codex looks. A server-side
 * render proves which state renders what; the session's ordering is proved by its own tests.
 */
import { describe, expect, it } from "vitest";
import { render } from "svelte/server";

import BackendGuidedSignIn from "../src/components/conversation/BackendGuidedSignIn.svelte";
import BackendSignInBlock from "../src/components/conversation/BackendSignInBlock.svelte";
import { backendCanBeReSignedIn, backendNeedsGuidedSignIn } from "../src/lib/conversation/backendSignIn";
import type { SignInPageState } from "../src/lib/conversation/backendSignInSession";
import type { BackendSignIn, BackendSnapshot } from "../src/lib/conversation/wire";

function snapshot(status: "authenticated" | "unauthenticated" | "unknown", detail: string | null = null): BackendSnapshot {
  return {
    backend_key: "codex", can_sign_in: true, installed: true, executable_path: null, version: null,
    identity: { status, account_label: null, detail, login_command: null },
    available_models: [], reasoning_effort_options: [], cached_usage: null, update_advisory: null,
    diagnoses: []
  };
}

function signIn(overrides: Partial<BackendSignIn>): BackendSignIn {
  return {
    session_id: "s1", state: "pending", verification_url: "https://auth.example.test/device",
    user_code: "ABCD-1234", started_at: "2026-11-17T00:00:00Z", deadline_at: "2026-11-17T00:10:00Z",
    detail: null, account_label: null, ...overrides
  };
}

function draw(card: BackendSnapshot, state: SignInPageState): string {
  return render(BackendSignInBlock, { props: {
    label: "Codex", snapshot: card, state, onStart: () => {}, onCancel: () => {}, onSubmit: () => {}
  } }).body;
}

const idle: SignInPageState = { signIn: null, busy: false, error: null };

describe("the sign-in block", () => {
  it("draws nothing for a signed-in account with no sign-in running", () => {
    expect(draw(snapshot("authenticated"), idle)).not.toContain("data-backend-sign-in");
  });

  it("offers Sign in to a signed-out account", () => {
    const body = draw(snapshot("unauthenticated"), idle);
    expect(body).toContain("data-backend-sign-in-start");
    expect(body).not.toContain("data-backend-sign-in-return-url");
  });

  it("shows the link and code while a device sign-in is pending, and no paste box", () => {
    const body = draw(snapshot("authenticated"), { ...idle, signIn: signIn({}) });
    expect(body).toContain("https://auth.example.test/device");
    expect(body).toContain("ABCD-1234");
    expect(body).toContain("data-backend-sign-in-cancel");
    expect(body).not.toContain("data-backend-sign-in-return-url");
  });

  it("shows the paste box only while the sign-in waits for the return address", () => {
    const body = draw(snapshot("unauthenticated"),
      { ...idle, signIn: signIn({ state: "awaiting_return_url", user_code: null }) });
    expect(body).toContain("data-backend-sign-in-return-url");
    expect(body).toContain("Return address");
    expect(body).toMatch(/<input[^>]*type="url"[^>]*required/);
    expect(body).toContain("https://auth.example.test/device");
    expect(body).toContain("data-backend-sign-in-submit");
    expect(body).toContain("data-backend-sign-in-cancel");
    expect(body).not.toContain("ABCD-1234");
  });

  it("keeps the paste box and shows the server's sentence after a refused submit", () => {
    const body = draw(snapshot("unauthenticated"), {
      busy: false, error: "that sign-in is not the one in force any more",
      signIn: signIn({ state: "awaiting_return_url", user_code: null })
    });
    expect(body).toContain("data-backend-sign-in-return-url");
    expect(body).toContain("that sign-in is not the one in force any more");
  });

  it("reports a settled sign-in on an account still signed out", () => {
    const body = draw(snapshot("unauthenticated"),
      { ...idle, signIn: signIn({ state: "unconfirmed", user_code: null }) });
    expect(body).toContain("Codex reported a sign-in and Panels could not confirm a usable credential.");
    expect(body).toContain("data-backend-sign-in-start");
  });
});

describe("expired and guided sign-in", () => {
  it("explains an expired Codex login and warns before a re-sign-in", () => {
    const expired = snapshot("unknown", "This saved login expired and codex has not refreshed it yet.");
    expect(backendCanBeReSignedIn(expired)).toBe(true);
    expect(backendCanBeReSignedIn(snapshot("authenticated"))).toBe(false);
    const body = draw(expired, idle);
    expect(body).toContain("This saved login expired");
    expect(body).toContain("data-backend-sign-in-caveat");
  });

  it("guides a signed-out backend that Panels cannot sign in, and shows its command to copy", () => {
    const claude = { ...snapshot("unauthenticated"), backend_key: "claude", can_sign_in: false,
      identity: { status: "unauthenticated", account_label: null, detail: null, login_command: "claude auth login" } } as BackendSnapshot;
    expect(backendNeedsGuidedSignIn(claude)).toBe(true);
    expect(backendNeedsGuidedSignIn(snapshot("unauthenticated"))).toBe(false);
    const body = render(BackendGuidedSignIn, { props: {
      label: "Claude", machineName: "Mac 1", command: "claude auth login", checking: false, onCheck: () => {}
    } }).body;
    expect(body).toContain("claude auth login");
    expect(body).toContain("Mac 1");
    expect(body).toContain("data-backend-guided-check");
  });
});
