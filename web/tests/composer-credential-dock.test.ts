import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import ComposerWithQueryClient from "./fixtures/ComposerWithQueryClient.svelte";
import type { CredentialRequestRowBody } from "../src/lib/conversation/view/contracts";

const request: CredentialRequestRowBody = {
  kind: "credential_request",
  request_id: "r-1",
  name: "SENTRY_AUTH_TOKEN",
  provider: "Sentry",
  use: "Upload dSYMs from CI after each TestFlight build.",
  auth_kind: "bearer",
  header_name: null,
  help: null,
  help_url: null,
  replacement: null,
  state: "pending",
  credential: null,
  delivery: null
};

const base = { draftKey: "composer-credential-dock", onSend: async () => true };

describe("a key request is asked in the composer's place", () => {
  it("is the composer while it waits: the ask stands inside the composer's own frame", () => {
    const { body } = render(ComposerWithQueryClient, { props: { ...base, credentialRequest: request } });
    expect(body).toContain("Needs a key");
    expect(body).toMatch(/data-conversation-ask-dock[^>]*data-conversation-taken-over="true"/);
    // One frame: the ask is inside the box, before the field, never a card above it.
    const box = body.indexOf("data-conversation-box");
    expect(box).toBeGreaterThan(-1);
    expect(body.indexOf("data-conversation-ask-dock")).toBeGreaterThan(box);
    expect(body.indexOf("data-conversation-ask-dock")).toBeLessThan(body.indexOf("data-conversation-input"));
    expect(body).toMatch(/class="chat-box[^"]*has-ask/);
    // The person can still write: the field is there, enabled, on the composer's one line.
    expect(body).not.toMatch(/<textarea[^>]*hidden/);
    expect(body).not.toMatch(/<textarea[^>]*disabled/);
    expect(body).toMatch(/class="chat-box[^"]*is-compact/);
  });

  it("is stopped from the ask's own last row while the turn waits on it, never from a round Stop", () => {
    const running = render(ComposerWithQueryClient, { props: { ...base, credentialRequest: request, running: true, onStop: () => {}, onCancelTurn: () => {} } }).body;
    expect(running).not.toContain("data-credential-cancel-turn");
    expect(running).not.toContain("chat-send stop");
    expect(running.match(/data-conversation-stop/g)?.length).toBe(1);
    expect(running).toMatch(/data-conversation-stop[^>]*data-conversation-ask-stop/);
    const idle = render(ComposerWithQueryClient, { props: { ...base, credentialRequest: request } }).body;
    expect(idle).not.toContain("data-conversation-stop");
  });

  it("waits behind a permission ask, which is answered first", () => {
    const ask = {
      askId: "ask-1",
      title: "Upload Wren build 41 to TestFlight",
      detail: null,
      options: [{ option_id: "allow", label: "Allow once", option_kind: "allow_once" as const }]
    };
    const { body } = render(ComposerWithQueryClient, { props: { ...base, ask, credentialRequest: request } });
    expect(body).toContain("Needs your approval");
    expect(body).not.toContain("Needs a key");
  });

  it("leaves the composer as it was with no request", () => {
    const { body } = render(ComposerWithQueryClient, { props: base });
    // Popovers overlay what stands over the box; the composer keeps no room that pushes it up.
    expect(body).not.toContain("chat-popover-room");
    expect(body).not.toContain("Needs a key");
    expect(body).toContain("data-conversation-add");
    expect(body).not.toContain("data-conversation-taken-over");
  });
});

import ConversationTranscript from "../src/components/conversation/ConversationTranscript.svelte";
import { DOCKED_CREDENTIAL_REQUEST, pendingCredentialRequest } from "../src/lib/conversation/composer";
import type { TranscriptRow } from "../src/lib/conversation/transcript";
import type { ConversationViewItem } from "../src/lib/conversation/view/contracts";

function credentialRow(sequence: number, body: CredentialRequestRowBody): TranscriptRow {
  return { key: `e${sequence}`, kind: "credential_request", sequence, createdAt: sequence, request: body };
}

function credentialItem(sequence: number, changed: number, body: CredentialRequestRowBody): ConversationViewItem {
  return {
    kind: "row", key: `r${sequence}`, row_kind: "credential_request", order: [sequence] as never,
    source_sequence: sequence, sender_message_id: null, changed_through_sequence: changed, created_at: sequence,
    turn_key: null, work_group_key: null, fold_rule: "never", body: { kind: "inline", value: body }
  } as ConversationViewItem;
}

describe("which key request is docked", () => {
  it("is the newest pending one in the rows; settled ones are not", () => {
    const older = { ...request, request_id: "r-0" };
    const settled = { ...request, request_id: "r-2", state: "rejected" as const };
    expect(pendingCredentialRequest([credentialRow(1, older), credentialRow(2, request), credentialRow(3, settled)])?.request_id).toBe("r-1");
    expect(pendingCredentialRequest([credentialRow(3, settled)])).toBeNull();
  });

  it("reads the live view's newest version of each item", () => {
    const saved = { ...request, state: "saved" as const };
    expect(pendingCredentialRequest([], [credentialItem(1, 1, request), credentialItem(1, 5, saved)])).toBeNull();
    expect(pendingCredentialRequest([], [credentialItem(1, 5, saved), credentialItem(1, 1, request)])).toBeNull();
    expect(pendingCredentialRequest([], [credentialItem(1, 1, request)])?.request_id).toBe("r-1");
  });
});

describe("the transcript leaves the docked request to the composer", () => {
  const row = credentialRow(4, request);
  const props = { rows: [], visibleRows: [], lens: "focus" as const, conversationId: "c", projectedItems: [{ kind: "row" as const, key: row.key, row, behindTheFoldOf: null }] };

  it("does not draw the request the composer asks", () => {
    const { body } = render(ConversationTranscript, { props, context: new Map([[DOCKED_CREDENTIAL_REQUEST, () => "r-1"]]) });
    expect(body).not.toContain("Needs a key");
  });

  it("draws any other request inline, and every request when none is docked", () => {
    expect(render(ConversationTranscript, { props, context: new Map([[DOCKED_CREDENTIAL_REQUEST, () => "r-9"]]) }).body).toContain("Needs a key");
    expect(render(ConversationTranscript, { props }).body).toContain("Needs a key");
  });
});
