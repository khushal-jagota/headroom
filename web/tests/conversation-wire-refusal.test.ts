import { afterEach, describe, expect, it, vi } from "vitest";
import { ConversationWireError, readBackends } from "../src/lib/conversation/wire";

afterEach(() => vi.unstubAllGlobals());

async function refusal(body: string, status: number): Promise<ConversationWireError> {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status })));
  const error = await readBackends().catch((caught: unknown) => caught);
  expect(error).toBeInstanceOf(ConversationWireError);
  return error as ConversationWireError;
}

describe("a refusal says the server's own sentence", () => {
  it("reads a transport detail", async () => {
    const error = await refusal(JSON.stringify({ detail: "This machine is not connected to Panels." }), 409);
    expect(error.message).toBe("This machine is not connected to Panels.");
    expect(error.status).toBe(409);
  });
  it("reads a planner error envelope's message", async () => {
    const error = await refusal(JSON.stringify({ error: { code: "validation", message: "ticket_id is required", detail: {} } }), 400);
    expect(error.message).toBe("ticket_id is required");
  });
  it("falls back to the status only when the server said nothing readable", async () => {
    expect((await refusal("<html>bad gateway</html>", 502)).message).toBe("The server answered 502.");
    expect((await refusal(JSON.stringify({ error: { message: "" } }), 400)).message).toBe("The server answered 400.");
  });
});
