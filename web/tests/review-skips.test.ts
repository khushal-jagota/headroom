import { expect, it } from "vitest";
import { isSkipped, pruneSkips, skipHistoryKeys, withSkip } from "../src/lib/review/skips";
import type { NavigationReviewItem } from "../src/lib/navigation/contracts";

const message = (id: string, waiting_since: number, latest_sequence: number | null = null) =>
  ({ review_item_type: "message", conversation_id: id, address_id: `a-${id}`, owner_kind: "ticket", owner_id: "t1", title: "T", waiting_since, latest_sequence, recap: "", recap_reference: null }) as const;
const ticket = (revision: number | null, messages: ReturnType<typeof message>[] = []) =>
  ({ review_item_type: "ticket", ticket_id: "t1", title: "T", waiting_since: 1, messages, ask: null,
    proposal: revision === null ? null : { review_item_type: "proposal", ticket_id: "t1", field: "brief", title: "T", waiting_since: 1, proposal_field: "brief", proposal_revision: revision } }) as unknown as NavigationReviewItem;

it("a skip holds until the card shows a new proposal revision, a new message or a new ask", () => {
  const list = withSkip({}, ticket(1, [message("c1", 5, 10)]));
  expect(isSkipped(list, ticket(1, [message("c1", 5, 10)]))).toBe(true);
  expect(isSkipped(list, ticket(2, [message("c1", 5, 10)]))).toBe(false);
  expect(isSkipped(list, ticket(1, [message("c1", 5, 11)]))).toBe(false);
  expect(isSkipped(list, ticket(1, [message("c1", 5, 10), message("c2", 6)]))).toBe(false);
});

it("pruning drops a changed card always and an absent card only when the whole queue is in view", () => {
  const list = withSkip(withSkip({}, ticket(1)), message("c9", 3) as unknown as NavigationReviewItem);
  expect(pruneSkips(list, [ticket(1)], false)).toBe(list);
  expect(skipHistoryKeys(pruneSkips(list, [ticket(1)], true))).toEqual([{ kind: "ticket", id: "t1" }]);
  expect(skipHistoryKeys(pruneSkips(list, [ticket(2)], false))).toEqual([{ kind: "conversation", id: "c9" }]);
});
