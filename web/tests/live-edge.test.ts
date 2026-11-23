import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import { SvelteSet } from "svelte/reactivity";
import LiveEdge from "../src/components/conversation/view/LiveEdge.svelte";
import { liveTurnOf, turnHeadAtTop } from "../src/lib/conversation/view/liveEdge";
import type { ConversationViewItem, ConversationViewStatus, ConversationViewTurn } from "../src/lib/conversation/view/contracts";
import type { ConversationViewReader } from "../src/lib/conversation/view/reader.svelte";

const turn = (settled: boolean): ConversationViewTurn => ({
  kind: "turn", key: "turn:e1", order: { sequence: 1, slot: 1 }, changed_through_sequence: 5,
  settled, stopped: false, started_at_unix_milliseconds: 100_000, ending: settled ? "completed" : null,
  duration_seconds: settled ? 140 : null, tool_call_count: 1, folded_message_count: 0, latest_message_key: null, is_latest: true
});
const runningTool: ConversationViewItem = {
  kind: "row", key: "tool:running", row_kind: "tool_call", order: { sequence: 2, slot: 1 }, source_sequence: 2,
  sender_message_id: null, changed_through_sequence: 2, created_at: 0, turn_key: "turn:e1", work_group_key: "g1", fold_rule: "work",
  body: { kind: "inline", value: { kind: "tool_call", tool_call_id: "t1", title: "Run go test", tool_kind: "execute", detail: null, started_detail: null, capped_detail_sequence: null, status: "running" } }
};
const statusWith = (extra: Partial<ConversationViewStatus> = {}): ConversationViewStatus => ({
  turn_key: "turn:e1", pending_permission: null, pending_user_input: null, stopped_without_ending: false, ...extra
} as ConversationViewStatus);
const reader = (lens: "focus" | "full", items: ConversationViewItem[], status = statusWith()): ConversationViewReader => ({
  id: "c", lens, pages: [{ scope: { kind: "timeline", key: null }, items }], status, openTools: new SvelteSet<string>()
} as unknown as ConversationViewReader);
const draw = (lens: "focus" | "full", items: ConversationViewItem[], status = statusWith()): string =>
  render(LiveEdge, { props: { reader: reader(lens, items, status) } }).body;

describe("which turn is live", () => {
  it("is a running turn that has not stopped; its head is the live edge, not its top", () => {
    expect(liveTurnOf([{ items: [turn(false)] }], statusWith())?.key).toBe("turn:e1");
    expect(liveTurnOf([{ items: [turn(true)] }], statusWith())).toBeNull();
    expect(liveTurnOf([{ items: [turn(false)] }], statusWith({ stopped_without_ending: true }))).toBeNull();
    expect(turnHeadAtTop(turn(false), statusWith())).toBe(false);
    expect(turnHeadAtTop(turn(true), statusWith())).toBe(true);
  });
});

describe("the live edge", () => {
  it("says Working with the clock and no narration sentence, in Focus and in Full", () => {
    for (const lens of ["focus", "full"] as const) {
      const html = draw(lens, [turn(false)]);
      expect(html).toContain("data-conversation-live-edge");
      expect(html).toContain("data-conversation-alive-word");
      expect(html).not.toContain("narration");
    }
  });

  it("has the spinner in Focus and in Full, even while a tool runs", () => {
    for (const lens of ["focus", "full"] as const) {
      const html = draw(lens, [turn(false), runningTool]);
      expect(html).toContain('data-conversation-system-mark="spinner"');
      expect(html).not.toContain("c2-meta-dot");
    }
  });

  it("is not drawn while the turn waits on an ask", () => {
    const waiting = statusWith({ pending_permission: { kind: "permission", request_id: "a", row_key: "e2", source_sequence: 2 } } as Partial<ConversationViewStatus>);
    expect(draw("focus", [turn(false)], waiting)).not.toContain("data-conversation-live-edge");
  });
});
