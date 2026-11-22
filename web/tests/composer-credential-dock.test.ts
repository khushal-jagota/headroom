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
  it("draws the request in the box, which gives way to it", () => {
    const { body } = render(ComposerWithQueryClient, { props: { ...base, credentialRequest: request } });
    expect(body).toContain("Needs your key");
    expect(body).toMatch(/data-conversation-box[^>]*data-conversation-taken-over="true"|data-conversation-taken-over="true"[^>]*data-conversation-box/);
    expect(body).toMatch(/<textarea[^>]*hidden/);
    // The box draws no add, model or send controls of its own under the request's actions.
    expect(body).not.toContain("data-conversation-add");
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
    expect(body).not.toContain("Needs your key");
  });

  it("leaves the composer as it was with no request", () => {
    const { body } = render(ComposerWithQueryClient, { props: base });
    expect(body).not.toContain("Needs your key");
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
    expect(body).not.toContain("Needs your key");
  });

  it("draws any other request inline, and every request when none is docked", () => {
    expect(render(ConversationTranscript, { props, context: new Map([[DOCKED_CREDENTIAL_REQUEST, () => "r-9"]]) }).body).toContain("Needs your key");
    expect(render(ConversationTranscript, { props }).body).toContain("Needs your key");
  });
});
