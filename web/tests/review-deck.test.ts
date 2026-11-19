import { expect, it } from "vitest";
import { deckDirectionBetween, FINAL_VIEW } from "../src/lib/review/deckMotion";

// The rule is that everything moves forward, except the one way back from the final screen.
it("moves forward between items and into the final screen, and back only out of the final screen", () => {
  expect(deckDirectionBetween("ticket:a", "ticket:b")).toBe(1);
  expect(deckDirectionBetween("ticket:a", FINAL_VIEW)).toBe(1);
  expect(deckDirectionBetween(FINAL_VIEW, "ticket:a")).toBe(-1);
  expect(deckDirectionBetween(null, "ticket:a")).toBe(1);
});
