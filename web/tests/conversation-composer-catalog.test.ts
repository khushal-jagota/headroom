import { describe, expect, it } from "vitest";

import { catalogTokenAtMessageStart } from "../src/lib/conversation/composerCatalog";

describe("Conversation composer catalog activation", () => {
  it.each([
    ["/", 1, "/", ""],
    ["$skill", 6, "$", "skill"],
    ["@plugin remaining text", 7, "@", "plugin"]
  ] as const)(
    "activates for %s when its token begins at absolute message offset zero",
    (written, at, trigger, typedSoFar) => {
      expect(catalogTokenAtMessageStart(written, at)).toMatchObject({
        start: 0,
        trigger,
        typedSoFar
      });
    }
  );

  it.each([
    [" /review", 8],
    ["\n/review", 8],
    ["first line\n/review", 18]
  ] as const)("does not activate for a displaced token in %j", (written, at) => {
    expect(catalogTokenAtMessageStart(written, at)).toBeNull();
  });

  it("stops activation after the cursor leaves the message-start token", () => {
    expect(catalogTokenAtMessageStart("/review more", 12)).toMatchObject({
      start: 0,
      end: 7,
      typedSoFar: null
    });
  });
});
