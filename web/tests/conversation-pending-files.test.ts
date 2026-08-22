import { describe, expect, it, vi } from "vitest";

import {
  CONVERSATION_FILE_ACCEPT,
  MAX_CONVERSATION_MESSAGE_FILE_BYTES,
  createPendingConversationFiles,
  pendingFilesAsPieces,
  restoredPendingFiles
} from "../src/lib/conversation/pendingFiles";

describe("pending Conversation files", () => {
  it.each([
    ["report.pdf", "application/pdf", "%PDF-1.7\nbody\n%%EOF"],
    ["notes.txt", "text/plain", "hello"],
    ["plan.md", "text/markdown", "# Plan"],
    ["rows.csv", "text/csv", "a,b\n1,2"],
    ["rows.tsv", "text/tab-separated-values", "a\tb"],
    ["data.json", "application/json", "{\"value\":7}"],
    ["events.jsonl", "application/x-ndjson", "{\"a\":1}\n{\"a\":2}\n"]
  ])("admits %s as canonical %s", async (fileName, mediaType, body) => {
    const intake = await createPendingConversationFiles(
      [new File([body], fileName, { type: "application/octet-stream" })],
      4
    );

    expect(intake.rejected).toEqual([]);
    expect(intake.accepted[0]).toMatchObject({ id: 4, fileName, mediaType });
    expect(pendingFilesAsPieces(intake.accepted)[0]).toMatchObject({
      piece: "file",
      media_type: mediaType,
      file_name: fileName
    });
  });

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

  it.each([
    " facts.json",
    "facts.json ",
    "folder/facts.json",
    "folder\\facts.json",
    "bad\u0000facts.json",
    "bad\u007ffacts.json",
    `${"a".repeat(251)}.json`
  ])("rejects unsafe file name %j before reading bytes", async (fileName) => {
    const file = new File(["{}"], fileName);
    const read = vi.fn(async () => new ArrayBuffer(2));
    file.arrayBuffer = read;

    const intake = await createPendingConversationFiles([file], 1);

    expect(intake.accepted).toEqual([]);
    expect(intake.rejected).toEqual([file]);
    expect(read).not.toHaveBeenCalled();
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
