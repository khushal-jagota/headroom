import { expect, it } from "vitest";
import { shownReviewItem } from "../src/lib/review/shown";

it("keeps the next card after approval when a refresh inserts a new one ahead of it", () => {
  const key = (item: { id: string }) => item.id;
  let shown = shownReviewItem([{ id: "A" }, { id: "B" }, { id: "C" }], null, key);
  expect(shown.item?.id).toBe("A");

  // Approving A drops it at once. The card that was next is B.
  shown = shownReviewItem([{ id: "B" }, { id: "C" }], shown.pinnedKey, key);
  expect(shown.item?.id).toBe("B");

  // The refresh arrives with a new card that sorts ahead of B. B stays.
  shown = shownReviewItem([{ id: "N" }, { id: "B" }, { id: "C" }], shown.pinnedKey, key);
  expect(shown.item?.id).toBe("B");
  shown = shownReviewItem([{ id: "M" }, { id: "N" }, { id: "B" }, { id: "C" }], shown.pinnedKey, key);
  expect(shown.item?.id).toBe("B");

  // When B itself leaves, the oldest card still waiting is chosen, and then kept.
  shown = shownReviewItem([{ id: "M" }, { id: "N" }, { id: "C" }], shown.pinnedKey, key);
  expect(shown.item?.id).toBe("M");
  shown = shownReviewItem([{ id: "P" }, { id: "M" }, { id: "N" }, { id: "C" }], shown.pinnedKey, key);
  expect(shown.item?.id).toBe("M");
});
