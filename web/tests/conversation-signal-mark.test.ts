import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import ConversationSignalMark from "../src/components/ConversationSignalMark.svelte";

describe("exact conversation signal marks", () => {
  it.each([
    { working: false, message_for_owner: false },
    { working: true, message_for_owner: false },
    { working: false, message_for_owner: true },
    { working: true, message_for_owner: true }
  ])("shows one mark with message precedence %j", (signal) => {
    const html = render(ConversationSignalMark, {
      props: { signal, conversationId: "conversation-exact" }
    }).body;
    expect(html.includes('data-conversation-signal="working"')).toBe(signal.working && !signal.message_for_owner);
    expect(html.includes('data-conversation-signal="message"')).toBe(signal.message_for_owner);
    expect(html.includes('stage-mark--current-running')).toBe(signal.working && !signal.message_for_owner);
    expect(html.includes('stage-mark--current-awaiting-approval')).toBe(signal.message_for_owner);
    if (signal.working || signal.message_for_owner) {
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
  it.each([
    { working: false, message_for_owner: false },
    { working: true, message_for_owner: false },
    { working: false, message_for_owner: true },
    { working: true, message_for_owner: true }
  ])("uses message precedence in Worker and supervisor rows: %j", (signal) => {
    const worker = render(SprintTicketRow, { props: {
      title: "Worker", signal, conversationId: "worker-exact"
    } }).body;
    const supervisor = render(WorkspaceRailCard, { props: {
      id: "item", title: "Supervisor", signal, conversationId: "supervisor-exact",
      selected: false, selectedTicketId: null, groups: [], onSelect: () => {},
      onTicket: () => {}, onSettle: () => {}, onHover: () => {}, onWorkerHover: () => {}, onLeave: () => {}
    } }).body;
    for (const html of [worker, supervisor]) {
      expect(html.includes('data-conversation-signal="working"')).toBe(signal.working && !signal.message_for_owner);
      expect(html.includes('data-conversation-signal="message"')).toBe(signal.message_for_owner);
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
