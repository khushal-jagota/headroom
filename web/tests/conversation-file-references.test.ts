import { describe, expect, it } from "vitest";
import { fileReferencePieces } from "../src/lib/markdownPipeline";

const references = (text: string) => fileReferencePieces(text).filter((piece) => piece.reference).map((piece) => piece.text);

describe("file references in plain words are code in the conversation (v4)", () => {
  it("marks a path that names a line or a directory", () => {
    expect(references("flush() swaps b.pending at batcher.go:97 while Add() reads it at batcher.go:61.")).toEqual(["batcher.go:97", "batcher.go:61"]);
    expect(references("see web/src/app.ts and server/main.go:12:4, then retry")).toEqual(["web/src/app.ts", "server/main.go:12:4"]);
  });

  it("leaves words alone: a bare file name, a product name, a version, a URL host", () => {
    expect(references("Node.js and README.md and v1.2.3 and example.com")).toEqual([]);
  });

  it("keeps every character, in order, so the text reads the same", () => {
    const text = "at batcher.go:97, then internal/push/batcher.go.";
    expect(fileReferencePieces(text).map((piece) => piece.text).join("")).toBe(text);
  });
});
