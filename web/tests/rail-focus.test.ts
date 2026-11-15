import { expect, it, vi } from "vitest";
import { focusVisibleGroups, keyTogglesRailFocus } from "../src/lib/railFocus.svelte";

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

it("Shift+F flips Focus, but plain F, other modifiers, repeats and typing do not", () => {
  class FakeElement { closest = (selector: string) => (selector.includes("textarea") && this.typing ? this : null); constructor(readonly typing = false) {} }
  vi.stubGlobal("Element", FakeElement);
  const press = (init: object, target?: FakeElement) =>
    keyTogglesRailFocus({ key: "F", shiftKey: true, target, ...init } as unknown as KeyboardEvent);
  expect(press({})).toBe(true);
  expect(press({ shiftKey: false, key: "f" })).toBe(false);
  expect(press({ ctrlKey: true })).toBe(false);
  expect(press({ repeat: true })).toBe(false);
  expect(press({}, new FakeElement(true))).toBe(false);
  vi.unstubAllGlobals();
});
