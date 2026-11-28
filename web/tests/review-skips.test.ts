import { afterEach, expect, it, vi } from "vitest";
import { applyingSkips, dropUnseen, isSkipped, limitSkips, parseSkips, skipHistoryKeys, SKIP_LIMIT, stringifySkips, withSkip, type SkipList } from "../src/lib/review/skips";
import type { NavigationReviewItem } from "../src/lib/navigation/contracts";

const message = (id: string, latest_sequence: number | null = null) =>
  ({ review_item_type: "message", conversation_id: id, address_id: `a-${id}`, owner_kind: "ticket", owner_id: "t1", title: "T", waiting_since: 5, latest_sequence, recap: "", recap_reference: null }) as const;
const ask = (id: string, waiting_since: number) => ({ review_item_type: "ask", conversation_id: id, waiting_since }) as const;
/** A Ticket card: its proposal by revision, its messages by conversation and sequence, and its ask. */
const ticket = (revision: number | null, messages: ReturnType<typeof message>[] = [], id = "t1", theAsk: ReturnType<typeof ask> | null = null, waiting_since = 1) =>
  ({ review_item_type: "ticket", ticket_id: id, title: "T", waiting_since: 1, messages, ask: theAsk,
    proposal: revision === null ? null : { review_item_type: "proposal", ticket_id: id, field: "brief", title: "T", waiting_since, proposal_field: "brief", proposal_revision: revision } }) as unknown as NavigationReviewItem;
const asCard = (value: unknown) => value as NavigationReviewItem;

afterEach(() => { vi.unstubAllGlobals(); vi.resetModules(); });

it("a skip ends when the card shows a newer or new proposal, message or ask", () => {
  const list = withSkip({}, ticket(1, [message("c1", 10)], "t1", ask("c1", 7)), 1);
  expect(isSkipped(list, ticket(1, [message("c1", 10)], "t1", ask("c1", 7)))).toBe(true);
  expect(isSkipped(list, ticket(2, [message("c1", 10)], "t1", ask("c1", 7), 2))).toBe(false);
  expect(isSkipped(list, ticket(1, [message("c1", 11)], "t1", ask("c1", 7)))).toBe(false);
  expect(isSkipped(list, ticket(1, [message("c1", 10), message("c2", 3)], "t1", ask("c1", 7)))).toBe(false);
  expect(isSkipped(list, ticket(1, [message("c1", 10)], "t1", ask("c1", 9)))).toBe(false);
  expect(isSkipped(list, ticket(1, [message("c1", 10)], "t1", ask("c2", 7)))).toBe(false);
  expect(isSkipped(withSkip({}, ticket(null, [message("c1", 10)]), 1), ticket(3, [message("c1", 10)], "t1", null, 4))).toBe(false);
});

it("a skip does not end when the card loses a part, or is read from an older view", () => {
  const list = withSkip({}, ticket(2, [message("c1", 10), message("c2", 4)], "t1", ask("c1", 7), 3), 1);
  // A message was answered, the ask was answered, the proposal was decided: the card is smaller, not new.
  expect(isSkipped(list, ticket(2, [message("c1", 10)], "t1", ask("c1", 7), 3))).toBe(true);
  expect(isSkipped(list, ticket(null, [message("c2", 4)]))).toBe(true);
  expect(isSkipped(list, ticket(null, []))).toBe(true);
  // A tab with an older view shows an older proposal and older sequences.
  expect(isSkipped(list, ticket(1, [message("c1", 9)], "t1", ask("c1", 6), 2))).toBe(true);
});

it("a skip made from an older view does not make a newer skip older", () => {
  const newer = withSkip({}, ticket(2, [message("c1", 10)], "t1", ask("c1", 7), 3), 1);
  const merged = withSkip(newer, ticket(1, [message("c1", 8)], "t1", ask("c1", 5), 2), 2);
  expect(isSkipped(merged, ticket(2, [message("c1", 10)], "t1", ask("c1", 7), 3))).toBe(true);
  expect(isSkipped(merged, ticket(2, [message("c1", 11)], "t1", ask("c1", 7), 3))).toBe(false);
});

it("the count holds the skips that apply to loaded cards, and the skips of cards not loaded", () => {
  let list = withSkip({}, ticket(1, [], "changed"), 1);
  list = withSkip(list, ticket(1, [], "same"), 2);
  list = withSkip(list, ticket(1, [], "beyond"), 3);
  const keys = skipHistoryKeys(applyingSkips(list, [ticket(2, [], "changed", null, 2), ticket(1, [], "same")])).map(key => key.id);
  expect(keys).toEqual(["beyond", "same"]);
});

it("a skip for a card in no loaded window is dropped, and the cap drops those cards first, oldest first", () => {
  let list: SkipList = {};
  for (let index = 0; index < SKIP_LIMIT + 2; index += 1) list = withSkip(list, ticket(1, [], `t${index}`), index);
  const key = (id: string) => JSON.stringify(["ticket", id]);
  expect(Object.keys(dropUnseen(list, new Set([key("t0"), key("t1")])))).toEqual([key("t0"), key("t1")]);
  // t0 and t1 are the oldest, and the page has seen them. The two oldest unseen skips go instead.
  const capped = limitSkips(list, new Set([key("t0"), key("t1")]));
  expect(Object.keys(capped)).toHaveLength(SKIP_LIMIT);
  expect(Object.keys(capped)).toContain(key("t0"));
  expect(Object.keys(capped)).not.toContain(key("t2"));
  expect(Object.keys(capped)).not.toContain(key("t3"));
  expect(Object.keys(capped)).toContain(key("t4"));
});

it("stored text that this build did not write reads as no skips and never throws", () => {
  const good = stringifySkips(withSkip({}, ticket(1), 1));
  expect(Object.keys(parseSkips(good))).toHaveLength(1);
  const oldShape = '{"version":1,"entries":{"[\\"ticket\\",\\"t1\\"]":{"fingerprint":"f","at":1}}}';
  for (const raw of [null, "", "garbage", "[]", "7", '{"[\\"ticket\\",\\"t1\\"]":"old"}', oldShape, '{"version":2,"entries":{}}', '{"version":1,"entries":[]}', '{"version":1}'])
    expect(parseSkips(raw)).toEqual({});
  // A bad entry is dropped and the good ones stay.
  const entry = (extra = "") => `{"marks":{"proposal":null,"messages":{},"asks":{}${extra}},"at":1}`;
  const mixed = `{"version":1,"entries":{"[\\"ticket\\",\\"a\\"]":${entry()},"[\\"ticket\\",\\"b\\"]":{"marks":{"proposal":null,"messages":{"c":"x"},"asks":{}},"at":1},"nonsense":${entry()}}}`;
  expect(Object.keys(parseSkips(mixed))).toEqual([JSON.stringify(["ticket", "a"])]);
});

/** One browser tab: its own copy of the store, over storage that every tab shares. */
async function openTab(storage: Map<string, string>, options: { blocked?: boolean } = {}) {
  const handlers: Array<(event: StorageEvent) => void> = [];
  const blocked = () => { if (options.blocked) throw new Error("blocked"); };
  vi.stubGlobal("window", {
    localStorage: {
      get length() { return storage.size; },
      key: (index: number) => [...storage.keys()][index] ?? null,
      getItem: (key: string) => { blocked(); return storage.get(key) ?? null; },
      setItem: (key: string, value: string) => { blocked(); storage.set(key, value); },
      removeItem: (key: string) => { blocked(); storage.delete(key); },
    },
    addEventListener: (_type: string, handler: (event: StorageEvent) => void) => handlers.push(handler),
  });
  vi.resetModules();
  const { reviewSkips } = await import("../src/lib/review/skipStore.svelte");
  const names = (list: SkipList) => Object.keys(list).map(entry => (JSON.parse(entry) as string[])[1]).sort();
  return { store: reviewSkips, ids: (scope: string) => names(reviewSkips.list(scope)), hear: (storageKey: string, newValue: string | null) => handlers.forEach(handler => handler({ key: storageKey, newValue } as StorageEvent)) };
}
const KEY = "panels:review-skipped:server-1";

it("two tabs keep each other's skips, and a skip in one reaches the other", async () => {
  const storage = new Map<string, string>();
  const a = await openTab(storage);
  a.store.skip("server-1", ticket(1, [], "x"));
  const b = await openTab(storage);
  expect(b.ids("server-1")).toEqual(["x"]);
  // Tab a has not heard of what b writes next. A skip from a must keep it.
  b.store.skip("server-1", ticket(1, [], "y"));
  a.store.skip("server-1", ticket(1, [], "z"));
  expect(Object.keys(parseSkips(storage.get(KEY)!)).length).toBe(3);
  expect(a.ids("server-1")).toEqual(["x", "y", "z"]);
  // Tab b hears a's write and holds all three.
  expect(b.ids("server-1")).toEqual(["x", "y"]);
  b.hear(KEY, storage.get(KEY)!);
  expect(b.ids("server-1")).toEqual(["x", "y", "z"]);
});

it("a tab that hears a change it already holds changes nothing, so nothing redraws", async () => {
  const storage = new Map<string, string>();
  const tab = await openTab(storage);
  tab.store.skip("server-1", ticket(1, [], "x"));
  const before = tab.store.list("server-1");
  tab.hear(KEY, storage.get(KEY)!);
  expect(tab.store.list("server-1")).toBe(before);
});

it("servers are kept apart, and another tab clearing storage empties the list", async () => {
  const storage = new Map<string, string>();
  const tab = await openTab(storage);
  tab.store.skip("server-1", ticket(1, [], "x"));
  expect(tab.ids("server-2")).toEqual([]);
  tab.store.skip("server-2", ticket(1, [], "z"));
  expect(tab.ids("server-1")).toEqual(["x"]);
  // Another tab removes one list: the other stays.
  storage.delete(KEY);
  tab.hear(KEY, null);
  expect(tab.ids("server-1")).toEqual([]);
  expect(tab.ids("server-2")).toEqual(["z"]);
  // Another tab clears storage: the event names no key.
  storage.clear();
  tab.hear(null as unknown as string, null);
  expect(tab.ids("server-2")).toEqual([]);
});

it("a partly answered card stays skipped when the queue refreshes", async () => {
  const tab = await openTab(new Map());
  tab.store.skip("server-1", ticket(2, [message("c1", 10)], "t1", ask("c1", 7), 3));
  // The reply is on its way and the refresh removes the message and the ask. Neither the refresh nor a walk drops the skip.
  const refreshed = ticket(2, [], "t1", null, 3);
  tab.store.observe("server-1", [refreshed], { first: true, last: true });
  expect(isSkipped(tab.store.list("server-1"), refreshed)).toBe(true);
  expect(tab.ids("server-1")).toEqual(["t1"]);
});

it("a walk from the top to the last window drops the skips of cards in no window, and a walk that did not start at the top drops nothing", async () => {
  const tab = await openTab(new Map());
  for (const id of ["a", "b", "gone"]) tab.store.skip("server-1", ticket(1, [], id));
  // The page opened at a later window: it has not seen the top, so it cannot say that a card left.
  tab.store.observe("server-1", [ticket(1, [], "b")], { first: false, last: true });
  expect(tab.ids("server-1")).toEqual(["a", "b", "gone"]);
  tab.store.observe("server-1", [ticket(1, [], "a")], { first: true, last: false });
  tab.store.observe("server-1", [ticket(1, [], "b")], { first: false, last: true });
  expect(tab.ids("server-1")).toEqual(["a", "b"]);
});

it("the page knows every window it has loaded, so a changed card past the first window leaves the count once its window loads", async () => {
  const tab = await openTab(new Map());
  for (const id of ["near", "far"]) tab.store.skip("server-1", ticket(1, [], id));
  tab.store.observe("server-1", [ticket(1, [], "near")], { first: true, last: false });
  const keys = () => skipHistoryKeys(applyingSkips(tab.store.list("server-1"), [...tab.store.known("server-1").values()])).map(key => key.id);
  // "far" is in a window that has not loaded: it counts.
  expect(keys()).toEqual(["far", "near"]);
  // Its window loads and shows a newer proposal: it no longer counts, though the first window is gone from the page.
  tab.store.observe("server-1", [ticket(2, [], "far", null, 2)], { first: false, last: false });
  expect(keys()).toEqual(["near"]);
  // The entry stays until the card leaves, so a tab with an older view cannot drop it.
  expect(tab.ids("server-1")).toEqual(["far", "near"]);
});

it("blocked storage leaves the skips in the page for the visit", async () => {
  const tab = await openTab(new Map(), { blocked: true });
  tab.store.skip("server-1", ticket(1, [], "x"));
  expect(tab.ids("server-1")).toEqual(["x"]);
});

it("a stored list with garbage in it is replaced by the next skip, and lists of servers abandoned long ago are removed", async () => {
  const old = Date.now() - 60 * 24 * 60 * 60 * 1000;
  const abandoned = stringifySkips({ [JSON.stringify(["ticket", "q"])]: { marks: { proposal: null, messages: {}, asks: {} }, at: old } });
  const recent = stringifySkips(withSkip({}, ticket(1, [], "r"), Date.now()));
  const storage = new Map([[KEY, "not json"], ["panels:review-skipped:server-old", abandoned], ["panels:review-skipped:server-live", recent]]);
  const tab = await openTab(storage);
  expect(tab.ids("server-1")).toEqual([]);
  tab.store.skip("server-1", ticket(1, [], "x"));
  expect(Object.keys(parseSkips(storage.get(KEY)!))).toHaveLength(1);
  expect(storage.has("panels:review-skipped:server-old")).toBe(false);
  expect(storage.has("panels:review-skipped:server-live")).toBe(true);
});
