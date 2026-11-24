import { describe, expect, it } from "vitest";
import { fileReferencePieces, inlineCodeSpacing } from "../src/lib/markdownPipeline";

const references = (text: string) => fileReferencePieces(text).filter((piece) => piece.reference).map((piece) => piece.text);

describe("file references in plain words are code in the conversation", () => {
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

describe("inline code spacing", () => {
  it("marks a code span followed by closing punctuation, and one that opens a list item", () => {
    // "at `batcher.go:97`, read" — the comma sits against the chip.
    expect(inlineCodeSpacing("P", ["at ", null, ", read"], 1)).toEqual(["cv-code-before-punctuation"]);
    expect(inlineCodeSpacing("P", ["at ", null, "."], 1)).toEqual(["cv-code-before-punctuation"]);
    expect(inlineCodeSpacing("P", ["at ", null, " while"], 1)).toEqual([]);
    // "- `flush()` swaps" — the chip opens the item, so its pad hangs outside the text edge.
    expect(inlineCodeSpacing("LI", [null, " swaps"], 0)).toEqual(["cv-code-leads"]);
    expect(inlineCodeSpacing("LI", ["\n", null, ")"], 1)).toEqual(["cv-code-before-punctuation", "cv-code-leads"]);
    expect(inlineCodeSpacing("LI", ["read ", null], 1)).toEqual([]);
    expect(inlineCodeSpacing("P", [null, " swaps"], 0)).toEqual([]);
  });
});
