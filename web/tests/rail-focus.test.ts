import { expect, it } from "vitest";
import { focusVisibleGroups } from "../src/lib/railFocus.svelte";

const card = (id: string, message: boolean) => ({ id, conversation_signal: { message_for_owner: message } });
const groups = [
  { key: "agent", cards: [card("quiet", false), card("loud", true)] },
  { key: "status_awaiting_approval", cards: [card("only-quiet", false)] },
  { key: "assigned", cards: [card("kept", false)] },
];

it("Focus keeps a hidden group's Tickets that have a message and drops empty groups", () => {
  const shown = focusVisibleGroups(groups, true);
  expect(shown.map(g => [g.key, g.cards.map(c => c.id)])).toEqual([["agent", ["loud"]], ["assigned", ["kept"]]]);
  expect(focusVisibleGroups(groups, false)).toBe(groups);
});
