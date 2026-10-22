import { managerPageGroups } from "../src/lib/managerPage";
import { workItemTicketGroupKey } from "../src/lib/workItemPresentation";
import { describe, expect, it, vi } from "vitest";
import { tick } from "svelte";
import type { BoardCard, BoardManager, ManagerWorkspaceTicket } from "../src/lib/types";
import { buildLifecycle, type WorkerTypeManifest } from "../src/lib/lifecycle";
import { buildWorkspaceRail, railTicketEligible, refreshWorkspaceRailSignals, stabilizeWorkspaceRail, workspaceWorkerStageSentence, workspaceListRecap } from "../src/lib/workspaceRail";
import { railDomMatches } from "../src/lib/workspaceRailDom";
import { createRailMotionController, type WorkspaceRailPresentation } from "../src/lib/workspaceRailMotion";

const quiet = {
  awaiting_reply: false, awaiting_answer: false, awaiting_approval: false,
  awaiting_agent_approval: false, assigned: false, agent_state: "idle" as const
};
const card = (id: string, values: Partial<BoardCard> = {}): BoardCard & ManagerWorkspaceTicket => ({
  id, title: id, recap:"", recap_preview:"", is_backlogged:false, activity_at: 0, stage: "needs_implementation",
  ticket_status: "agent", worker_type: "coding", manager_id: null,
  ...quiet, ...values
} as BoardCard & ManagerWorkspaceTicket);
const item = (id: string, values: Partial<BoardManager> = {}): BoardManager => ({
  id, title: id, recap: "",recap_preview: "", created_at: 1, updated_at:1, tickets: [], conversation_id: null,
  ticket_rollup: quiet, ...quiet, ...values
});

describe("manager rail projection", () => {
  it("keeps active empty and completed managers until explicit Settle", () => {
    const rail = buildWorkspaceRail([], [
      item("empty"), item("complete", { tickets: [card("done", { stage: "done", ticket_status: "empty" })] }),
      item("settled", { settled: true, tickets: [card("question", { awaiting_answer: true })] })
    ]);
    expect(rail.items.map((row) => row.id)).toEqual(["complete", "empty"]);
    expect(rail.items.every((row) => !row.rested && !row.settled)).toBe(true);
    expect(rail.settledItems.map((row) => row.id)).toEqual(["settled"]);
    expect(rail.settledItems[0].groups[0].key).toBe("awaiting_answer");
  });

  it("keeps Chief ownership distinct and retains complete child facts", () => {
    const rail = buildWorkspaceRail([
      card("legacy-chief", { assigned: true }),
      card("explicit-chief", { manager: { kind: "chief", id: "chief" }, awaiting_reply: true }),
      card("ordinary", { manager: { kind: "manager", id: "ordinary" }, manager_id: "ordinary", assigned: true })
    ], [item("ordinary", { tickets: [
      card("complete-child", { awaiting_approval: true }), card("agent-child"),
      card("historical", { stage: "done", ticket_status: "empty" })
    ] })]);
    expect(rail.chiefGroups.flatMap((group) => group.cards.map((row) => row.id))).toEqual(["legacy-chief", "explicit-chief"]);
    expect(rail.items[0].groups.flatMap((group) => group.cards.map((row) => row.id))).toEqual(["complete-child", "agent-child"]);
    expect(rail.items[0].tickets).toHaveLength(3);
    expect(rail.items[0].conversationSignal).toBeUndefined();
  });

  it("excludes backlog from Chief, active and settled projections without changing child facts", () => {
    const rows = [card("approval", { is_backlogged:true, awaiting_approval:true }),
      card("manager", { is_backlogged:true, awaiting_agent_approval:true }),
      card("message", { is_backlogged:true, awaiting_reply:true }),
      card("quiet", { is_backlogged:true }),
      card("done", { is_backlogged:true, stage:"done", ticket_status:"empty" })];
    const rail = buildWorkspaceRail(rows, [item("ordinary", { tickets:rows }), item("settled", { tickets:rows, settled:true })]);
    for (const groups of [rail.chiefGroups, rail.items[0].groups, rail.settledItems[0].groups]) {
      expect(groups).toEqual([]);
    }
    expect(rail.groups.map((group) => group.key)).toEqual(["backlog"]);
    expect(rail.items[0].tickets).toEqual(rows);
    expect(rail.settledItems[0].tickets).toEqual(rows);
    const resumed = rows.map((row) => ({...row, is_backlogged:false}));
    expect(buildWorkspaceRail(resumed).chiefGroups.map((group) => group.key))
      .toEqual(["awaiting_approval", "agent"]);
    expect(rows[0].awaiting_approval).toBe(true);
  });

  it("filters ineligible overlap before grouping and keeps quiet work in the sidebar", () => {
    const rows = [card("working", { conversation_signal:{working:true,message_for_owner:false} }),
      card("closeout", { ticket_status:"empty", waiting_to_closeout:true }),
      card("agent-review", { ticket_status:"empty", awaiting_agent_approval:true }),
      card("idle", { ticket_status:"empty" }),
      card("blocked-status", {ticket_status:"blocked", agent_state:"working", awaiting_approval:true}),
      ...["done", "backlog", "blocked"].map((id) => card(id, {
        awaiting_approval:true, stage:id === "done" ? "done" : "needs_plan",
        is_backlogged:id === "backlog", blocked:id === "blocked", ticket_status:"agent"
      }))];
    const rail = buildWorkspaceRail(rows);
    expect(rail.chiefGroups.map((group) => [group.label, group.cards.map((row) => row.id)]))
      .toEqual([["Agent", ["working"]], ["Waiting on Consequences", ["closeout"]],
        ["Awaiting an agent's approval", ["agent-review"]], ["Other", ["idle"]]]);
    expect(rail.groups.find((group) => group.key === "empty")?.label).toBe("Empty");
  });

  it("keeps unread independent of rail categories while preserving global classification and original records", () => {
    const rows = [
      card("working", { awaiting_reply:true, conversation_signal:{working:true,message_for_owner:true} }),
      card("quiet", { ticket_status:"empty", awaiting_reply:true, conversation_signal:{working:false,message_for_owner:true} }),
      card("approval", { awaiting_reply:true, awaiting_approval:true }),
      card("answer", { awaiting_reply:true, awaiting_answer:true }),
      card("assigned", { awaiting_reply:true, assigned:true }),
      card("error", { awaiting_reply:true, agent_state:"errored" }),
      card("recent", {activity_at:20}),
      card("tie-b", {title:"Same", activity_at:10}),
      card("tie-a", {title:"Same", activity_at:10})
    ];
    const originals = structuredClone(rows);
    const before = buildWorkspaceRail(rows);
    expect(before.chiefGroups.map((group) => [group.key, group.cards.map((row) => row.id)]))
      .toEqual([["awaiting_approval", ["approval"]], ["awaiting_answer", ["answer"]],
        ["assigned", ["assigned"]], ["errored", ["error"]], ["agent", ["recent", "tie-a", "tie-b", "working"]], ["empty", ["quiet"]]]);
    const page = managerPageGroups(rows.filter(railTicketEligible));
    const presentation = page.map((group) => ({key:group.key,
      label:group.key === "empty" ? "Other" : group.label,
      defaultCollapsed:group.quiet, cards:group.tickets}));
    const withManagers = buildWorkspaceRail(rows, [item("active", {tickets:rows}),
      item("settled", {tickets:rows, settled:true})]);
    for (const groups of [before.chiefGroups, withManagers.items[0].groups, withManagers.settledItems[0].groups]) {
      expect(groups).toEqual(presentation);
      for (const row of rows) expect(groups.flatMap((group) => group.cards).find((entry) => entry.id === row.id)).toBe(row);
    }
    expect(rows).toEqual(originals);
    expect(workItemTicketGroupKey(rows[0])).toBe("awaiting_reply");
    expect(before.groups.find((group) => group.key === "awaiting_reply")?.cards.map((row) => row.id))
      .toEqual(["error", "quiet", "working"]);
    const after = stabilizeWorkspaceRail(buildWorkspaceRail(rows.map((row) => ({...row,
      awaiting_reply:false, conversation_signal:row.conversation_signal
        ? {...row.conversation_signal, message_for_owner:false} : undefined }))), before);
    const categories = (rail: typeof before) => rail.chiefGroups.map((group) => [group.key, group.cards.map((row) => row.id)]);
    expect(categories(after)).toEqual(categories(before));
    expect(after.chiefGroups.flatMap((group) => group.cards).every((row) => !row.awaiting_reply && !row.conversation_signal?.message_for_owner)).toBe(true);
    expect(after.chiefGroups.find((group) => group.key === "agent")?.cards.find((row) => row.id === "working")?.conversation_signal?.working).toBe(true);
  });

  it("passes the authoritative recap through and returns nothing when it is absent", () => {
    const source = item("recap", {recap:"Full body.\n" + "The rest of the recap is available. ".repeat(12),recap_preview:"Full body.…", tickets:[card("live"), card("done", {stage:"done"})]});
    const rail = buildWorkspaceRail([], [source]);
    expect(rail.items[0].recap).toBe(source.recap);
    expect(workspaceListRecap(rail.items[0].recap)).toBe(source.recap);
    expect(workspaceListRecap(" ")).toBe("");
    expect(workspaceListRecap()).toBe("");
  });

  it("retains survivors through refetch and appends entries and re-entries per category", async () => {
    const initial = buildWorkspaceRail([card("a", {activity_at:20}), card("b", {activity_at:10})]);
    const refreshed = stabilizeWorkspaceRail(buildWorkspaceRail([
      card("b", {activity_at:90}), card("a", {activity_at:1}), card("new", {activity_at:100})]), initial);
    expect(refreshed.chiefGroups[0].cards.map((row) => row.id)).toEqual(["a", "b", "new"]);
    const moved = stabilizeWorkspaceRail(buildWorkspaceRail([
      card("a", {awaiting_approval:true}), card("b"), card("new")]), refreshed);
    expect(moved.chiefGroups.map((group) => group.key)).toEqual(["awaiting_approval", "agent"]);
    const reentered = stabilizeWorkspaceRail(buildWorkspaceRail([card("a"), card("b"), card("new")]), moved);
    expect(reentered.chiefGroups[0].cards.map((row) => row.id)).toEqual(["b", "new", "a"]);
    expect(reentered.chiefGroups[0].cards[2].awaiting_approval).toBe(false);
    // Hover defers reassignment and new groups, while the exact conversation stays live.
    const updated = buildWorkspaceRail([], [item("new-parent", { tickets: [
      card("a", {awaiting_approval:true, title:"Changed title", conversation_id:"rebound",
        conversation_signal:{working:true,message_for_owner:false}})
    ] })]);
    const frozen = refreshWorkspaceRailSignals(reentered, updated);
    expect(frozen.chiefGroups[0].cards.map((row) => row.id)).toEqual(["b", "new", "a"]);
    expect(frozen.items).toEqual([]);
    expect(frozen.chiefGroups[0].cards[2]).toEqual({...reentered.chiefGroups[0].cards[2],
      conversation_id:"rebound", conversation_signal:{working:true,message_for_owner:false}});
    // A pointer can arrive after drain captures the answer but before exits settle.
    let presented: WorkspaceRailPresentation = {rail:reentered, settledOpen:false};
    const motion = createRailMotionController(() => null, () => presented, (next) => presented = next);
    motion.update({...presented, rail:updated});
    motion.freeze(true);
    await tick();
    expect(presented.rail).toEqual(frozen);
    motion.freeze(false);
    await tick();
    expect(presented.rail).toEqual(stabilizeWorkspaceRail(updated, frozen));
    motion.dispose();

  });

  it("acknowledges manual folds during a frozen rail exit and admits late pages without another entrance", async () => {
    let presented: WorkspaceRailPresentation = {rail:buildWorkspaceRail([card("departing")]), settledOpen:false};
    const plays: {node:string; frames:Keyframe[]; duration:number; delay:number; canceled:boolean; finish:()=>void}[] = [];
    const attributes = new Set<string>();
    let focused: unknown = null;
    let bodyHeight = 80;
    let prefersReduced = false;
    class Surface {
      activeHeight: {effect:{target:Surface}|null; frames:Keyframe[]} | null = null;
      dataset: Record<string,string> = {};
      parentElement = null;
      style = {overflow:"", height:"", setProperty() {}, removeProperty(key:string) { if (key === "overflow" || key === "height") this[key] = ""; }};
      classList = {add() {}, remove() {}, contains:()=>false};
      constructor(readonly name:string) { this.dataset.railMotion = name; }
      getBoundingClientRect() {
        const bodyOpen = presented.settledBodyOpen ?? presented.settledOpen;
        return {height:this.name === "settled-section" && this.activeHeight?.effect ? parseFloat(String(this.activeHeight.frames[0].height)) : this.name === "settled-section" && this.style.height ? parseFloat(this.style.height) : this.name === "settled-section" ? 20 + (bodyOpen && presented.rail.settledItems.length ? bodyHeight : 0) : 20, top:0,left:0};
      }
      closest() { return null; }
      contains(value:unknown) { return this === body && value === focused; }
      focus() { focused = this; }
      querySelector(selector:string) {
        if (this !== section) return null;
        if (selector === ".settled-head") return header;
        if (selector === ".settled-chevron") return chevron;
        if (selector === "[data-settled-scroll]") return (presented.settledBodyOpen ?? presented.settledOpen) ? body : null;
        return null;
      }
      animate(frames:Keyframe[], options:KeyframeAnimationOptions) {
        let finish!:()=>void;
        const finished = new Promise<void>(resolve => finish = resolve);
        const play = {node:this.name,frames,duration:Number(options.duration),delay:options.delay ?? 0,canceled:false,finish};
        plays.push(play);
        const animation = {finished,frames,effect:{target:this} as {target:Surface}|null,cancel:()=>{play.canceled=true;if(this.activeHeight===animation)this.activeHeight=null;finish();}};
        if (frames[0].height !== undefined) {
          this.activeHeight = animation;
          void finished.then(()=>{if(this.activeHeight===animation)this.activeHeight=null;});
        }
        return animation;
      }
    }
    const section = new Surface("settled-section"), header = new Surface("header"), chevron = new Surface("chevron"), body = new Surface("body");
    const departing = new Surface("row:departing"), late = new Surface("settled:late");
    const railRoot = {
      querySelector:(selector:string)=>selector === ".settled" ? section : null,
      querySelectorAll:()=>[section, ...(presented.rail.chiefGroups.length ? [departing] : []),
        ...((presented.settledBodyOpen ?? presented.settledOpen) && presented.rail.settledItems.length ? [late] : [])],
      setAttribute:(key:string)=>attributes.add(key), removeAttribute:(key:string)=>attributes.delete(key)
    } as unknown as HTMLElement;
    vi.stubGlobal("HTMLElement", Surface);
    vi.stubGlobal("getComputedStyle", (node:Surface)=>({backgroundColor:"white",color:"black",opacity:"1",
      transform:node === chevron && presented.settledOpen ? "rotate(180deg)" : "none"}));
    vi.stubGlobal("document", {get activeElement() { return focused; },createElement:()=>({getContext:()=>({fillStyle:"",fillRect() {},getImageData:()=>({data:[255,255,255,255]})})})});
    vi.stubGlobal("window", {matchMedia:()=>({get matches() {return prefersReduced;},addEventListener() {},removeEventListener() {}})});
    const flush = async () => { for (let i=0;i<12;i++) await tick(); };
    const motion = createRailMotionController(()=>railRoot, ()=>presented, next=>presented=next);
    try {
      motion.update({rail:buildWorkspaceRail([]), settledOpen:false});
      const unrelated = plays.find(play=>play.node === "row:departing")!;
      expect(unrelated.duration).toBe(160);
      motion.freeze(true);
      motion.setSettledOpen(true);
      expect(presented.settledOpen).toBe(true);
      await flush();
      const fold = plays.filter(play=>play.node === "chevron" || play.node === "body");
      expect(fold.map(play=>[play.duration,play.delay])).toEqual([[340,0],[340,0]]);
      for (const play of fold) play.finish();
      await flush();
      // A coherent first page arrives after the fold beat. The Worker exit
      // remains unresolved and hovered, but it cannot hold this page back.
      const child = card("child", {conversation_id:"child-conversation",conversation_signal:{working:false,message_for_owner:true}});
      const page:WorkspaceRailPresentation = {rail:buildWorkspaceRail([], [item("late", {settled:true,tickets:[child]})]), settledOpen:false};
      motion.update(page);
      expect(presented.rail.settledItems.map(item=>item.id)).toEqual(["late"]);
      expect(presented.settledOpen).toBe(true);
      await flush();
      const lateResize = plays[3];
      expect(lateResize.node).toBe("settled-section");
      expect(lateResize.frames).toEqual([{height:"20px"}, {height:"100px"}]);
      expect(lateResize.duration).toBe(340);
      // A signal-only refresh retains its beat; new natural geometry retargets
      // only height, and an obsolete completion cannot release the new owner.
      motion.update(page);
      await flush();
      expect(plays).toHaveLength(4);
      expect(lateResize.canceled).toBe(false);
      bodyHeight = 120;
      motion.update(page);
      await flush();
      const retarget = plays[4];
      expect(lateResize.canceled).toBe(true);
      expect(retarget.node).toBe("settled-section");
      expect(retarget.frames[1]).toEqual({height:"140px"});
      lateResize.finish();
      await flush();
      expect(section.style.overflow).toBe("hidden");
      retarget.finish();
      await flush();
      expect(section.style.height).toBe("");
      expect(section.style.overflow).toBe("");
      focused = {};
      motion.setSettledOpen(false);
      expect(presented.settledOpen).toBe(false);
      expect(presented.settledBodyOpen).toBe(true);
      expect(focused).toBe(header);
      await flush();
      const closing = plays.slice(5);
      expect(closing.map(play=>[play.duration,play.delay])).toEqual([[340,0],[340,0],[340,0]]);
      // Navigation withdraws the Settled scope. The manager body stays during its fade.
      const readChild = {...child,conversation_signal:{working:false,message_for_owner:false}};
      motion.update({...page,rail:buildWorkspaceRail([], [item("late", {settled:true,tickets:[readChild]})])});
      const closed:WorkspaceRailPresentation = {rail:buildWorkspaceRail([]),settledOpen:false};
      motion.update(closed);
      await flush();
      expect(presented.settledBodyOpen).toBe(true);
      expect(presented.rail.settledItems.map(item=>item.id)).toEqual(["late"]);
      motion.setSettledOpen(true);
      expect(closing.every(play=>play.canceled)).toBe(true);
      expect(unrelated.canceled).toBe(false);
      const reopened = {...page,rail:buildWorkspaceRail([], [item("late", {settled:true,title:"Fresh page",tickets:[readChild]})])};
      motion.update(reopened);
      expect(presented.rail.settledItems[0].title).toBe("Fresh page");
      await flush();
      for (const play of plays.slice(8)) play.finish();
      await flush();
      expect(presented.settledOpen).toBe(true);
      expect(presented.settledBodyOpen).toBe(true);
      // The old queued snapshot is allowed to finish its Worker transition,
      // but its closed flag cannot revert the newer manual choice.
      motion.freeze(false);
      unrelated.finish();
      await flush();
      expect(presented.settledOpen).toBe(true);
      expect(presented.rail.chiefGroups).toEqual([]);
      expect(presented.rail.settledItems.map(item=>item.id)).toEqual(["late"]);
      expect(plays.some(play=>play.node === "settled:late")).toBe(false);
      expect(attributes.has("data-rail-motion-busy")).toBe(false);
      const beforeClose = plays.length;
      motion.setSettledOpen(false);
      await flush();
      motion.update(closed);
      await flush();
      expect(presented.rail.settledItems[0].title).toBe("Fresh page");
      expect(presented.settledBodyOpen).toBe(true);
      for (const play of plays.slice(beforeClose)) play.finish();
      await flush();
      expect(presented.settledBodyOpen).toBe(false);
      expect(presented.rail.settledItems).toEqual([]);
      prefersReduced = true;
      const beforeReduced = plays.length;
      motion.setSettledOpen(true);
      motion.update(reopened);
      await flush();
      motion.setSettledOpen(false);
      await flush();
      expect(presented.settledBodyOpen).toBe(false);
      expect(plays).toHaveLength(beforeReduced);
    } finally { motion.dispose(); vi.unstubAllGlobals(); }
  });

  it("preserves active creation order, sorts Settled by update time, and keeps exact direct signals", () => {
    const rail = buildWorkspaceRail([], [
      item("newer", { created_at: 20,updated_at:100 }), item("older", { created_at: 10 }),
      item("urgent", { created_at: 0, awaiting_reply: true, conversation_signal: {working:true,message_for_owner:true} }),
      item("settled-old", {settled:true,created_at:100,updated_at:1}),
      item("settled-new", {settled:true,created_at:1,updated_at:30}),
      item("settled-tie", {settled:true,created_at:0,updated_at:30}),
      item("children-message", { ticket_rollup: { ...quiet, awaiting_reply: true } })
    ]);
    expect(rail.items.map((row) => row.id)).toEqual(["urgent", "children-message", "older", "newer"]);
    expect(rail.settledItems.map(row=>[row.id,row.updatedAt])).toEqual([["settled-new",30],["settled-tie",30],["settled-old",1]]);
    expect(rail.items.map((row) => row.conversationSignal)).toEqual([{working:true,message_for_owner:true}, undefined, undefined, undefined]);
  });

  it("credits only manager order, matching controls, and no Settled message rows", () => {
    // A selector host tests acknowledgment refusal without another browser host.
    const rail=buildWorkspaceRail([], [item("older",{settled:true,updated_at:1}),item("newer",{settled:true,updated_at:2})]);
    const title=(text:string,label:string)=>({textContent:text,getAttribute:()=>label,querySelector:()=>null});
    let reopenLabel="Reopen newer",openLabel="Open newer",message=false;
    const rows=rail.settledItems.map(row=>({dataset:{settledItem:row.id},querySelector:(selector:string)=>
      selector==='.settled-title > span' ? title(row.title,'') : selector==='.settled-title'
        ? title(row.title,row.id==='newer'?openLabel:`Open ${row.title}`)
        : selector==='.reopen' ? title('Reopen',row.id==='newer'?reopenLabel:`Reopen ${row.title}`) : null}));
    const chief={querySelectorAll:()=>[],querySelector:(selector:string)=>selector==='.item-head .item-title'?title('Chief of Staff',''):{querySelector:()=>null}};
    let visible=rows;
    const root={querySelectorAll:(selector:string)=>selector==='.active-work-scroll > .manager-card'?[chief]:visible,
      querySelector:(selector:string)=>selector==='[data-settled-count]'?title('(2)',''):message?{}:null} as unknown as HTMLElement;
    const matches=(open=true)=>railDomMatches(root,rail,null,open);
    expect(matches()).toBe(true);
    visible=[...rows].reverse();expect(matches()).toBe(false);visible=rows;
    reopenLabel='Reopen older';expect(matches()).toBe(false);reopenLabel='Reopen newer';
    openLabel='Open older';expect(matches()).toBe(false);openLabel='Open newer';
    message=true;expect(matches()).toBe(false);
    visible=[];expect(matches(false)).toBe(false);
    message=false;expect(matches(false)).toBe(true);
  });

  it("states only manifest stages and facts that prove work", () => {
    const lifecycle = buildLifecycle({
      worker_type: "bespoke", label: "Bespoke", stages: [
        { id: "draft", label: "Draft", gating_field: "draft", is_terminal: false, ownership_mode: "worker" },
        { id: "review", label: "Review", gating_field: "review", is_terminal: false, ownership_mode: "user" },
        { id: "done", label: "Done", gating_field: null, is_terminal: true, ownership_mode: null }
      ], advance: {}, fields: [], ceiling_range: [], default_ceiling: "done",
      worker_profile_id: "bespoke", default_backend: "codex", default_model: null, default_reasoning_effort: null
    } as WorkerTypeManifest);
    const reviewing = card("reviewing", {stage:"review", ticket_status:"agent"});
    expect(workspaceWorkerStageSentence(reviewing, lifecycle)).toBe("At Review.");
    expect(workspaceWorkerStageSentence({...reviewing, conversation_signal:{working:true,message_for_owner:false}}, lifecycle)).toBe("Currently doing Review.");
    expect(workspaceWorkerStageSentence({...reviewing, awaiting_approval:true, conversation_signal:{working:true,message_for_owner:false}}, lifecycle)).toBe("Awaiting your approval at Review.");
    expect(workspaceWorkerStageSentence({...reviewing, agent_state:"errored"}, lifecycle)).toBe("The last turn failed at Review.");
    expect(workspaceWorkerStageSentence({...reviewing, blocked:true, conversation_signal:{working:true,message_for_owner:false}}, lifecycle)).toBe("Blocked at Review.");
    expect(workspaceWorkerStageSentence(reviewing, null)).toBe("");
  });
});

// Shared-publication adoption: these are browser adapters, not a new Signal policy.
import { createNavigationCollection } from '../src/lib/navigation/collection';
import { createNavigationKnowledge } from '../src/lib/navigation/knowledge';
import { navigationWorkspacePresentation, navigationTicketMasthead, navigationReviewPresentation, reviewProposalDetailMatches } from '../src/lib/navigation/presentation';
import { currentSnapshot, conversation, continuity, envelope } from './support/navigation/fixtures';
import { encodeCursor } from '../src/lib/navigation/validation';
import type { TicketRecord, NavigationRecord } from '../src/lib/navigation/contracts';
const sourceTicket:TicketRecord={kind:'ticket',id:'source-ticket',value:{
  title:'Source Ticket',recap:'Full source recap.\n'+'Body continues. '.repeat(20),recap_preview:'Source recap',worker_type:'coding',stage:'needs_implementation',ticket_status:'empty',blocked:false,created_at:1,
  updated_at:11,worker_step_claim_out:false,deadline:null,project_id:'project',manager_kind:'manager',
  manager_id:'item',conversation_id:'conversation',is_backlogged:false,ceiling:'needs_implementation',
  ceiling_holder:{kind:'owner',id:'owner'},gating_field:'implementation',proposal_field:null,proposal_revision:0,
  proposal_created_at:null,stage_ownership:'worker',terminal:false,locale:'en'}};
const sourceRecords:NavigationRecord[]=[sourceTicket,conversation('conversation'),
  {kind:'manager',id:'item',value:{title:'Parent',recap:'Full parent recap.\n'+'Body continues. '.repeat(20),recap_preview:'Parent recap',created_at:1,updated_at:11,project_id:'project',settled:false,conversation_id:null}},
  {kind:'project',id:'project',value:{name:'Project'}},
  {kind:'chief',id:'chief',value:{label:'Chief',conversation_id:null}},
  {kind:'address',id:'address',value:{owner_kind:'ticket',owner_id:'source-ticket',conversation_id:'conversation',current:true,parent_kind:'manager',parent_id:'item'}}];

describe('canonical navigation workspace presentation',()=>{
  it('joins masthead Project through the assigned parent without a raw Ticket fallback',()=>{
    const adapter=createNavigationCollection(),knowledge=createNavigationKnowledge();
    try {
      const read=(records:NavigationRecord[])=>{
        adapter.publishSnapshot(currentSnapshot({...continuity,records}),'masthead');
        return navigationTicketMasthead(knowledge.accept(adapter.getPublication()!),sourceTicket.id);
      };
      expect(read(sourceRecords)).toMatchObject({id:sourceTicket.id,title:'Source Ticket',project:'Project',conversation_id:'conversation',
        conversation_signal:{working:true,message_for_owner:true}});
      const distinctParent:NavigationRecord[]=sourceRecords.map(record=>record.kind==='manager' ? {...record,value:{...record.value,project_id:'parent-project'}} : record);
      distinctParent.push({kind:'project',id:'parent-project',value:{name:'Parent Project'}});
      expect(read(distinctParent)?.project).toBe('Parent Project');
      expect(read(sourceRecords.filter(record=>record.kind!=='manager'))).toBeUndefined();
      expect(read(sourceRecords.map(record=>record.kind==='ticket' ? {...record,value:{...record.value,manager_id:null,manager_kind:'chief'}} : record))?.project).toBe('Project');
      expect(read(sourceRecords.filter(record=>record.kind!=='ticket'))).toBeUndefined();
    } finally {adapter.destroy();}
  });
  it('preserves independent canonical structural facts and exact known/unknown conversation values',()=>{
    const adapter=createNavigationCollection(),knowledge=createNavigationKnowledge();
    try {
      const source={...sourceTicket,value:{...sourceTicket.value,ticket_status:'awaiting_approval',blocked:true,
        stage_ownership:'user',ceiling_holder:{kind:'owner',id:'owner'}}};
      adapter.publishSnapshot(currentSnapshot({...continuity,records:[source,...sourceRecords.slice(1)]}),'source');
      const view=knowledge.accept(adapter.getPublication()!);
      const first=navigationWorkspacePresentation(view);
      expect(first.tickets[0].recap).toBe(sourceTicket.value.recap);
      const parent=sourceRecords.find(record=>record.kind==='manager')!;
      expect(first.managers[0].recap).toBe(parent.value.recap);
      expect(first.managers[0].updated_at).toBe(11);
      expect(first.tickets[0]).toMatchObject({blocked:true,assigned:true,awaiting_approval:true,awaiting_agent_approval:false,
        agent_state:'working',awaiting_reply:true,conversation_signal:{working:true,message_for_owner:true}});
      expect(buildWorkspaceRail(first.tickets,first.managers).items[0].groups).toEqual([]);
      const retained=navigationWorkspacePresentation(knowledge.loseRuntime()!);
      expect(retained.tickets[0]).toMatchObject({agent_state:'errored',awaiting_reply:true,awaiting_answer:null,
        conversation_signal:{message_for_owner:true}});
      expect(retained.tickets[0].conversation_signal).not.toHaveProperty('working');
      expect(retained.managers[0].tickets).toHaveLength(1);
      expect(retained.managers[0].project).toBe('Project');
      knowledge.invalidateDurableFact('conversation','message_for_owner',10);
      const unknown=navigationWorkspacePresentation(knowledge.getView()!);
      expect(unknown.tickets[0].awaiting_reply).toBeNull();
      expect(unknown.tickets[0].conversation_signal).not.toHaveProperty('message_for_owner');
      expect(unknown.tickets[0].agent_state).toBe('errored');
      const done={...source,value:{...source.value,stage:'done',ticket_status:'empty',terminal:true}};
      adapter.publishSnapshot(currentSnapshot({...continuity,revision:'1',records:[done,...sourceRecords.slice(1)]}),'done');
      knowledge.accept(adapter.getPublication()!);knowledge.loseRuntime();
      expect(navigationWorkspacePresentation(knowledge.getView()!).tickets[0].agent_state).toBeNull();
      const agentApproval={...done,value:{...done.value,ticket_status:'awaiting_approval',ceiling_holder:{kind:'owner',id:'another'}}};
      adapter.publishSnapshot(currentSnapshot({...continuity,revision:'2',records:[agentApproval]}),'agent-approval');
      const last=navigationWorkspacePresentation(knowledge.accept(adapter.getPublication()!)).tickets[0];
      expect(last.awaiting_approval).toBe(false);expect(last.awaiting_agent_approval).toBe(true);
    } finally {adapter.destroy();}
  });
  it('moves the Consequences category from the same publication even while the label manifest stays stale',()=>{
    const adapter=createNavigationCollection(),knowledge=createNavigationKnowledge();
    const staleLabels=buildLifecycle({worker_type:'coding',label:'Coding',stages:[{id:'needs_implementation',label:'Implementation',gating_field:'implementation',is_terminal:false,ownership_mode:'worker'}],advance:{},fields:[{id:'implementation',label:'Implementation'}],ceiling_range:[],default_ceiling:'needs_implementation',worker_profile_id:'worker',default_backend:'codex',default_model:null,default_reasoning_effort:null});
    const records=[{...sourceTicket,value:{...sourceTicket.value,conversation_id:null}},...sourceRecords.slice(2)];
    const seen:string[]=[];
    adapter.subscribe(publication=>{
      const projected=navigationWorkspacePresentation(knowledge.accept(publication));
      seen.push(buildWorkspaceRail(projected.tickets,projected.managers).items[0].groups[0].key);
    });
    try {
      adapter.publishSnapshot(currentSnapshot({...continuity,records}),'before');
      const batch={...continuity,base_revision:'0',revision:'1',upserts:[{...sourceTicket,value:{...sourceTicket.value,conversation_id:null,gating_field:'consequences'}}],deletes:[]};
      adapter.publishBatch(envelope(batch),encodeCursor(batch),'gating-only');
      expect(seen).toEqual(['empty','waiting_to_closeout']);
      expect(staleLabels.gatingField.needs_implementation).toBe('implementation');
      const ticket=navigationWorkspacePresentation(knowledge.getView()!).tickets[0];
      expect(ticket.gating_field).toBe('consequences');
      expect(workspaceWorkerStageSentence(ticket,staleLabels)).toBe('At Implementation.');
    } finally {adapter.destroy();}
  });
  it('retains exact historical unread addresses independently of current conversation and visible sidebar classification',()=>{
    const adapter=createNavigationCollection(),knowledge=createNavigationKnowledge();
    const historical:NavigationRecord={kind:'address',id:'historical',value:{owner_kind:'ticket',owner_id:'source-ticket',conversation_id:'past',current:false,parent_kind:'manager',parent_id:'item'}};
    try {
      adapter.publishSnapshot(currentSnapshot({...continuity,records:[...sourceRecords,conversation('past'),historical]}),'aliases');
      const projected=navigationWorkspacePresentation(knowledge.accept(adapter.getPublication()!));
      expect(projected.messages.map(row=>[row.id,row.conversationId,row.parentId])).toEqual([
        ['source-ticket','conversation','item'],['source-ticket','past','item']]);
      expect(projected.tickets[0].conversation_id).toBe('conversation');
      expect(projected.messages.every(row=>row.conversationSignal?.message_for_owner)).toBe(true);
      const readPast=conversation('past',20,false);
      readPast.value.attention.current!.signal.working=false;
      const batch={...continuity,base_revision:'0',revision:'1',upserts:[readPast],deletes:[]};
      adapter.publishBatch(envelope(batch),encodeCursor(batch),'read-history');
      const afterRead=navigationWorkspacePresentation(knowledge.accept(adapter.getPublication()!));
      expect(afterRead.messages.map(row=>row.conversationId)).toEqual(['conversation']);
      expect(afterRead.conversationSignals.get('past')).toEqual({working:false,message_for_owner:false});
      expect(afterRead.conversationSignals.get('conversation')).toEqual({working:true,message_for_owner:true});
      expect(afterRead.conversationSignals.get('deleted-history')).toBeUndefined();
    } finally {adapter.destroy();}
  });
});


describe('shared Review and runtime counts',()=>{
  it('keeps canonical queue ordering, current-pointer union and all outclaims independent through atomic loss and replacement',()=>{
    const adapter=createNavigationCollection(),knowledge=createNavigationKnowledge();
    const ask=(id:string,stamp:number,changes:Partial<TicketRecord['value']>={}):TicketRecord=>({kind:'ticket',id,value:{...sourceTicket.value,
      ticket_status:'awaiting_approval',proposal_field:'plan',gating_field:'implementation',proposal_created_at:stamp,proposal_revision:1,...changes}});
    const records:NavigationRecord[]=[ask('b',2,{is_backlogged:true,blocked:true,terminal:true,worker_step_claim_out:true}),
      ask('c',2),ask('a',2),ask('earliest',1),ask('manager',0,{ceiling_holder:{kind:'chief',id:'chief'}}),
      ask('claim-only',0,{ticket_status:'empty',worker_step_claim_out:true,conversation_id:null}),
      ...sourceRecords.slice(1).filter(record=>record.kind!=='chief').map(record=>record.kind==='manager' ? {...record,value:{...record.value,conversation_id:'manager-current'}} : record),conversation('historical'),conversation('manager-current'),
      {kind:'chief',id:'chief',value:{label:'Chief',conversation_id:'conversation'}},
      {kind:'address',id:'history',value:{owner_kind:'ticket',owner_id:'a',conversation_id:'historical',current:false,parent_kind:'chief',parent_id:'chief'}}];
    try {
      const initial=records.find(record=>record.kind==='conversation' && record.id==='conversation');
      if (initial?.kind==='conversation') initial.value.attention.current!.signal.working=false;
      const manager=records.find(record=>record.kind==='conversation' && record.id==='manager-current');
      if (manager?.kind==='conversation') manager.value.attention.current!.signal.working=false;
      adapter.publishSnapshot(currentSnapshot({...continuity,records}),'counts');
      let view=knowledge.accept(adapter.getPublication()!);
      expect(navigationReviewPresentation(view)).toMatchObject({runningConversationCount:0,exactOutClaimCount:2,
        items:[{ticket_id:'earliest',field:'implementation',proposal_field:'plan'},{ticket_id:'a'},{ticket_id:'c'}]});
      const active=conversation('conversation');active.value.attention.current!.signal.working=true;
      const historical=conversation('historical');historical.value.attention.current!.signal.working=true;
      adapter.publishBatch(envelope({...continuity,base_revision:'0',revision:'1',upserts:[active,historical,conversation('manager-current')],deletes:[]}),encodeCursor({...continuity,revision:'1'}),'counts-batch');
      view=knowledge.accept(adapter.getPublication()!);
      expect(navigationReviewPresentation(view).runningConversationCount).toBe(2);
      const lost=knowledge.loseRuntime()!;
      expect(navigationReviewPresentation(lost)).toMatchObject({runningConversationCount:null,exactOutClaimCount:2});
      // Backlogged b stays outside Review but still contributes its outclaim.
      expect(navigationReviewPresentation(lost).items.map(item=>item.ticket_id)).toEqual(['earliest','a','c']);
      const missing={...view,conversations:new Map([...view.conversations].filter(([id])=>id!=='conversation'))};
      expect(navigationReviewPresentation(missing).runningConversationCount).toBeNull();
      const empty={...view,publication:{...view.publication,records:[]}};
      expect(navigationReviewPresentation(empty).runningConversationCount).toBe(0);
      // Identity is stricter than same-field membership; a newer body cannot wear an older stamp.
      const item=navigationReviewPresentation(view).items[0];
      const detail={id:item.ticket_id,pending_proposal:{field:'plan',created_at:1,revision:2,body:'replacement',proposed_by:'agent'}};
      expect(reviewProposalDetailMatches(item,detail)).toBe(false);
    } finally {adapter.destroy();}
  });
});
