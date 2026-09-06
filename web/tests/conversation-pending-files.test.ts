import { describe, expect, it, vi } from "vitest";

import {
  CONVERSATION_FILE_ACCEPT,
  MAX_CONVERSATION_MESSAGE_FILE_BYTES,
  createPendingConversationFiles,
  pendingFilesAsPieces,
  restoredPendingFiles
} from "../src/lib/conversation/pendingFiles";

describe("pending Conversation files", () => {
  it("publishes the narrow picker extension list", () => {
    expect(CONVERSATION_FILE_ACCEPT).toBe(
      ".pdf,.txt,.md,.markdown,.csv,.tsv,.json,.jsonl"
    );
  });

  it("rejects unsupported, malformed, non-UTF-8, empty, and aggregate overflow files", async () => {
    const tooLarge = new File(
      [new Uint8Array(MAX_CONVERSATION_MESSAGE_FILE_BYTES)],
      "too-large.txt"
    );
    const unread = vi.fn(async () => {
      throw new Error("aggregate overflow must not be read");
    });
    tooLarge.arrayBuffer = unread;
    const files = [
      new File([], "empty.txt"),
      new File(["PK"], "archive.zip"),
      new File(["not pdf"], "report.pdf"),
      new File(["%PDF-1.7\nwithout trailer"], "unfinished.pdf"),
      new File(["{"], "bad.json"),
      new File(["{\"ok\":1}\nno"], "bad.jsonl"),
      new File([new Uint8Array([0xff])], "binary.txt"),
      tooLarge
    ];

    const intake = await createPendingConversationFiles(files, 1, 1);

    expect(intake.accepted).toEqual([]);
    expect(intake.rejected).toEqual(files);
    expect(unread).not.toHaveBeenCalled();
  });

  it("restores file pieces in order with decoded byte counts", () => {
    expect(restoredPendingFiles([
      { piece: "text", text: "look" },
      {
        piece: "file",
        data: "e30=",
        media_type: "application/json",
        file_name: "facts.json"
      }
    ], 9)).toEqual({
      files: [{
        id: 9,
        fileName: "facts.json",
        mediaType: "application/json",
        byteCount: 2,
        data: "e30="
      }],
      nextId: 10
    });
  });
});
