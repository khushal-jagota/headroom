import { describe, expect, it } from "vitest";
import { fitTitle, TITLE_ELLIPSIS } from "../src/lib/conversation/titleFit";

describe("a truncated title", () => {
  const fitsIn = (width: number) => (candidate: string): boolean => candidate.length <= width;
  it("keeps a title that fits", () => {
    expect(fitTitle("Wren public beta", fitsIn(40))).toBe("Wren public beta");
  });
  it("trims the space a cut after a word leaves, so no space stands before the ellipsis", () => {
    const shown = fitTitle("Fix the flaky TestPushMergesConcurrentEdits on CI", fitsIn(45));
    expect(shown).toBe(`Fix the flaky TestPushMergesConcurrentEdits${TITLE_ELLIPSIS}`);
    expect(shown).not.toMatch(/\s…$/);
  });
  it("is the longest prefix that fits", () => {
    expect(fitTitle("abcdefghij", fitsIn(6))).toBe(`abcde${TITLE_ELLIPSIS}`);
    expect(fitTitle("abcdefghij", fitsIn(0))).toBe(TITLE_ELLIPSIS);
  });
});
