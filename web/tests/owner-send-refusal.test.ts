/**
 * A refusal from an owner send route is a refusal, not a send nobody answered.
 *
 * The Ticket, Manager and Chief composers send through `mutateJson`, which fails with
 * `PlannerFetchError` rather than the conversation wire's own error. The composer reads
 * both through `theServerRefusedTheSend`: a refusal returns the words with the server's
 * sentence, and anything else keeps the message as "no answer came / Send again".
 */
import { describe, expect, it, vi } from "vitest";

import { mutateJson } from "../src/lib/mutate";
import { theServerRefusedTheSend } from "../src/lib/conversation/outgoing";

async function failedSend(): Promise<unknown> {
  try {
    await mutateJson("/api/tickets/t_1/conversation/send", { method: "POST", body: {} });
  } catch (error) {
    return error;
  }
  throw new Error("the send was expected to fail");
}

describe("an owner send the server refused", () => {
  it.each([
    ["the planner error envelope", 400, { error: { code: "validation", message: "unknown agent backend" } }, "unknown agent backend"],
    ["a detail sentence", 422, { detail: "a message permits only one video" }, "a message permits only one video"]
  ])("is a refusal carrying the server's words, from %s", async (_shape, status, body, sentence) => {
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify(body), { status }));
    const error = await failedSend();
    expect(theServerRefusedTheSend(error)).toBe(true);
    expect((error as Error).message).toBe(sentence);
  });

  it("is not a refusal when no answer came", async () => {
    vi.stubGlobal("fetch", async () => { throw new TypeError("fetch failed"); });
    expect(theServerRefusedTheSend(await failedSend())).toBe(false);
  });

  it("is not a refusal when the server fell over", async () => {
    vi.stubGlobal("fetch", async () => new Response("Internal Server Error", { status: 500 }));
    expect(theServerRefusedTheSend(await failedSend())).toBe(false);
  });
});
