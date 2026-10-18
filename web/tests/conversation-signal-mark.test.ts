import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import ConversationSignalMark from "../src/components/ConversationSignalMark.svelte";

describe("exact conversation signal marks", () => {
  it.each([
    [{ working: false, message_for_owner: false }, null, null],
    [{ working: true, message_for_owner: false }, "working", "Working"],
    [{ working: false, message_for_owner: true }, "message", "Unread message"],
    [{ working: true, message_for_owner: true }, "message", "Unread message"]
  ] as const)("shows one mark with message precedence %j", (signal, mark, label) => {
    const html = render(ConversationSignalMark, {
      props: { signal, conversationId: "conversation-exact" }
    }).body;
    if (mark === null) expect(html).not.toContain('data-conversation-signal');
    else {
      expect(html).toContain(`data-conversation-signal="${mark}"`);
      expect(html).toContain(`aria-label="${label}"`);
      expect(html).toContain('data-conversation-id="conversation-exact"');
    }
  });

  it("keeps an absent conversation quiet", () => {
    const html = render(ConversationSignalMark, { props: { signal: null } }).body;
    expect(html).not.toContain("data-conversation-signal");
  });
});

import SprintTicketRow from "../src/components/SprintTicketRow.svelte";
import WorkspaceRailCard from "../src/components/WorkspaceRailCard.svelte";

describe("shared conversation signal consumers", () => {
  it("forwards mixed signal identity through the actual Worker and supervisor consumers", () => {
    const signal = { working: true, message_for_owner: true };
    const worker = render(SprintTicketRow, { props: {
      title: "Worker", signal, conversationId: "worker-mixed-exact"
    } }).body;
    const supervisor = render(WorkspaceRailCard, { props: {
      id: "item-mixed", title: "Supervisor", signal, conversationId: "supervisor-mixed-exact",
      selected: false, selectedTicketId: null, groups: [], onSelect: () => {},
      onTicket: () => {}, onSettle: () => {}, onHover: () => {}, onWorkerHover: () => {}, onLeave: () => {}
    } }).body;
    for (const [html, id] of [[worker, "worker-mixed-exact"], [supervisor, "supervisor-mixed-exact"]]) {
      expect(html).toContain(`data-conversation-id="${id}"`);
      expect(html).toContain('data-conversation-signal="message"');
      expect(html).toContain('aria-label="Unread message"');
      expect(html).not.toContain('data-conversation-signal="working"');
    }
  });

  it("does not let child activity supply a Chief mark", () => {
    const html = render(WorkspaceRailCard, { props: {
      id: "chief", title: "Chief", chief: true, selected: false, selectedTicketId: null,
      groups: [{key:"awaiting_approval",label:"Needs your approval",defaultCollapsed:false,cards:[{
        id:"child",title:"Child",stage:"done",ticket_status:"empty",
        awaiting_reply:false,awaiting_answer:false,awaiting_approval:true,awaiting_agent_approval:false,
        assigned:false,agent_state:"idle",conversation_id:"child-exact",
        conversation_signal:{working:true,message_for_owner:true}
      }]}], onSelect: () => {}, onTicket: () => {}, onSettle: () => {},
      onHover: () => {}, onWorkerHover: () => {}, onLeave: () => {}
    } }).body;
    expect(html).toContain('data-conversation-id="child-exact"');
    expect(html).not.toContain('data-conversation-signal="working"');
    expect(html.match(/data-conversation-signal="message"/g)).toHaveLength(1);
  });
});
