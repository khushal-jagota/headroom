import { afterEach, expect, it, vi } from "vitest";
import { isSkipped, limitSkips, parseSkips, pruneSkips, skipHistoryKeys, SKIP_LIMIT, stringifySkips, withSkip, type SkipList } from "../src/lib/review/skips";
import type { NavigationReviewItem } from "../src/lib/navigation/contracts";

const message = (id: string, waiting_since: number, latest_sequence: number | null = null) =>
  ({ review_item_type: "message", conversation_id: id, address_id: `a-${id}`, owner_kind: "ticket", owner_id: "t1", title: "T", waiting_since, latest_sequence, recap: "", recap_reference: null }) as const;
const ticket = (revision: number | null, messages: ReturnType<typeof message>[] = [], id = "t1") =>
  ({ review_item_type: "ticket", ticket_id: id, title: "T", waiting_since: 1, messages, ask: null,
    proposal: revision === null ? null : { review_item_type: "proposal", ticket_id: id, field: "brief", title: "T", waiting_since: 1, proposal_field: "brief", proposal_revision: revision } }) as unknown as NavigationReviewItem;

afterEach(() => { vi.unstubAllGlobals(); vi.resetModules(); });

it("a skip holds until the card shows a new proposal revision, a new message or a new ask", () => {
  const list = withSkip({}, ticket(1, [message("c1", 5, 10)]), 1);
  expect(isSkipped(list, ticket(1, [message("c1", 5, 10)]))).toBe(true);
  expect(isSkipped(list, ticket(2, [message("c1", 5, 10)]))).toBe(false);
  expect(isSkipped(list, ticket(1, [message("c1", 5, 11)]))).toBe(false);
  expect(isSkipped(list, ticket(1, [message("c1", 5, 10), message("c2", 6)]))).toBe(false);
});

it("pruning drops a changed card always, and an absent card only when the whole queue is in view", () => {
  const list = withSkip(withSkip({}, ticket(1), 1), message("c9", 3) as unknown as NavigationReviewItem, 2);
  expect(pruneSkips(list, [ticket(1)], false)).toBe(list);
  expect(skipHistoryKeys(pruneSkips(list, [ticket(1)], true))).toEqual([{ kind: "ticket", id: "t1" }]);
  expect(skipHistoryKeys(pruneSkips(list, [ticket(2)], false))).toEqual([{ kind: "conversation", id: "c9" }]);
});

it("a list never holds more than the limit, and the oldest skips go first", () => {
  let list: SkipList = {};
  for (let index = 0; index < SKIP_LIMIT + 3; index += 1) list = withSkip(list, ticket(1, [], `t${index}`), index);
  expect(Object.keys(list)).toHaveLength(SKIP_LIMIT);
  expect(Object.keys(list)).not.toContain(JSON.stringify(["ticket", "t0"]));
  expect(Object.keys(limitSkips(list, 2))).toEqual([JSON.stringify(["ticket", `t${SKIP_LIMIT + 2}`]), JSON.stringify(["ticket", `t${SKIP_LIMIT + 1}`])]);
});

it("stored text that this build did not write reads as no skips and never throws", () => {
  const good = stringifySkips(withSkip({}, ticket(1), 1));
  expect(Object.keys(parseSkips(good))).toHaveLength(1);
  for (const raw of [null, "", "garbage", "[]", "7", '{"[\\"ticket\\",\\"t1\\"]":"old-v0-shape"}', '{"version":2,"entries":{}}', '{"version":1,"entries":[]}', '{"version":1}'])
    expect(parseSkips(raw)).toEqual({});
  // A bad entry is dropped and the good ones stay.
  const mixed = `{"version":1,"entries":{"[\\"ticket\\",\\"a\\"]":{"fingerprint":"f","at":1},"[\\"ticket\\",\\"b\\"]":{"fingerprint":3,"at":1},"nonsense":{"fingerprint":"f","at":1}}}`;
  expect(Object.keys(parseSkips(mixed))).toEqual([JSON.stringify(["ticket", "a"])]);
});

/** One browser tab: its own copy of the store, over storage that every tab shares. */
async function openTab(storage: Map<string, string>, options: { blocked?: boolean } = {}) {
  const handlers: Array<(event: StorageEvent) => void> = [];
  vi.stubGlobal("window", {
    localStorage: {
      getItem: (key: string) => { if (options.blocked) throw new Error("blocked"); return storage.get(key) ?? null; },
      setItem: (key: string, value: string) => { if (options.blocked) throw new Error("blocked"); storage.set(key, value); },
      removeItem: (key: string) => { if (options.blocked) throw new Error("blocked"); storage.delete(key); },
    },
    addEventListener: (_type: string, handler: (event: StorageEvent) => void) => handlers.push(handler),
  });
  vi.resetModules();
  const { reviewSkips } = await import("../src/lib/review/skipStore.svelte");
  const key = (list: SkipList) => Object.keys(list).map(entry => (JSON.parse(entry) as string[])[1]).sort();
  return { store: reviewSkips, ids: (scope: string) => key(reviewSkips.list(scope)), hear: (storageKey: string, newValue: string | null) => handlers.forEach(handler => handler({ key: storageKey, newValue } as StorageEvent)) };
}

it("two tabs keep each other's skips, and a skip in one reaches the other", async () => {
  const storage = new Map<string, string>();
  const a = await openTab(storage);
  a.store.skip("server-1", ticket(1, [], "x"));
  const b = await openTab(storage);
  expect(b.ids("server-1")).toEqual(["x"]);
  // Both tabs write before either hears the other: nothing is lost.
  b.store.skip("server-1", ticket(1, [], "y"));
  expect(JSON.parse(storage.get("panels:review-skipped:server-1")!).entries).toHaveProperty([JSON.stringify(["ticket", "x"])]);
  const merged = storage.get("panels:review-skipped:server-1")!;
  expect(Object.keys(parseSkips(merged))).toHaveLength(2);
  // Tab a hears b's write and holds both.
  expect(a.ids("server-1")).toEqual(["x"]);
  a.hear("panels:review-skipped:server-1", merged);
  expect(a.ids("server-1")).toEqual(["x", "y"]);
});

it("a tab that hears a change it already holds changes nothing, so nothing redraws", async () => {
  const storage = new Map<string, string>();
  const tab = await openTab(storage);
  tab.store.skip("server-1", ticket(1, [], "x"));
  const before = tab.store.list("server-1");
  tab.hear("panels:review-skipped:server-1", storage.get("panels:review-skipped:server-1")!);
  expect(tab.store.list("server-1")).toBe(before);
});

it("another server's skips never apply, and another tab clearing storage empties the list", async () => {
  const storage = new Map<string, string>();
  const tab = await openTab(storage);
  tab.store.skip("server-1", ticket(1, [], "x"));
  expect(tab.ids("server-2")).toEqual([]);
  tab.store.skip("server-2", ticket(1, [], "z"));
  expect(tab.ids("server-1")).toEqual(["x"]);
  storage.clear();
  tab.hear("panels:review-skipped:server-1", null);
  expect(tab.ids("server-1")).toEqual([]);
  expect(tab.ids("server-2")).toEqual(["z"]);
});

it("blocked storage leaves the skips in the page for the visit", async () => {
  const tab = await openTab(new Map(), { blocked: true });
  tab.store.skip("server-1", ticket(1, [], "x"));
  expect(tab.ids("server-1")).toEqual(["x"]);
});

it("a stored list with garbage in it is dropped when the next skip is written", async () => {
  const storage = new Map([["panels:review-skipped:server-1", "not json"]]);
  const tab = await openTab(storage);
  expect(tab.ids("server-1")).toEqual([]);
  tab.store.skip("server-1", ticket(1, [], "x"));
  expect(Object.keys(parseSkips(storage.get("panels:review-skipped:server-1")!))).toHaveLength(1);
});
