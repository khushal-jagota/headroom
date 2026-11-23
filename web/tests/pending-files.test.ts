import { describe, expect, it } from "vitest";
import { conversationFileMediaType, createPendingConversationFiles } from "../src/lib/conversation/pendingFiles";

describe("plain-text log files", () => {
  it("are taken as text like a .txt", async () => {
    expect(conversationFileMediaType("ci-run.log")).toBe("text/plain");
    const file = new File(["FAIL push 4.5s\n"], "ci-run.log", { type: "" });
    const intake = await createPendingConversationFiles([file], 1);
    expect(intake.rejected).toEqual([]);
    expect(intake.accepted[0]).toMatchObject({ fileName: "ci-run.log", mediaType: "text/plain" });
  });

  it("still refuses a binary file named .log", async () => {
    const file = new File([new Uint8Array([0, 159, 146, 150])], "core.log");
    expect((await createPendingConversationFiles([file], 1)).rejected).toHaveLength(1);
  });
});
