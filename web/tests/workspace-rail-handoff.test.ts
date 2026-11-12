import {describe,it,expect,vi} from 'vitest';
import {tick} from 'svelte';
import {createRailMotionController,type WorkspaceRailPresentation} from '../src/lib/workspaceRailMotion';
import {buildWorkspaceRail} from '../src/lib/workspaceRail';
import type {WorkspaceTicket} from '../src/lib/workspaceRail';
import {railHandoff,emptyRailHandoffState,createRailHandoffController} from '../src/lib/workspaceRailHandoff';
const ticket = (id='a', extra:Partial<WorkspaceTicket>={}):WorkspaceTicket => ({id,title:id,manager_id:null,manager_title:null,stage:'needs_implementation',ticket_status:'agent',awaiting_reply:false,awaiting_answer:false,awaiting_approval:false,awaiting_agent_approval:false,assigned:false,agent_state:'idle',...extra});
const contexts = new Map([['chief','runtime:scope:window']]);
const group = (rail:ReturnType<typeof buildWorkspaceRail>,id='a') => rail.chiefGroups.find(group=>group.cards.some(ticket=>ticket.id===id))?.key;

describe('bounded rail handoff',()=>{
  it('retains fresh facts through short Agent and approval handoffs and never renews Empty',()=>{
    for(const from of [{},{awaiting_approval:true},{awaiting_agent_approval:true,ticket_status:'empty'}]) {
      const original=buildWorkspaceRail([ticket('a',from)]);
      const first=railHandoff(emptyRailHandoffState(),original,buildWorkspaceRail([]),contexts,0);
      const fresh=ticket('a',{ticket_status:'empty',title:'Fresh title',stage:'needs_consequences'});
      const empty=buildWorkspaceRail([fresh]);
      const held=railHandoff(first.state,empty,original,contexts,10);
      expect(group(held.rail)).toBe(group(original));
      expect(held.rail.chiefGroups[0].cards[0]).toBe(fresh);
      const repeat=railHandoff(held.state,empty,held.rail,contexts,900);
      expect(repeat.nextDeadline).toBe(1010);
      const expired=railHandoff(repeat.state,empty,repeat.rail,contexts,1010);
      expect(group(expired.rail)).toBe('empty');
      expect(railHandoff(expired.state,empty,held.rail,contexts,1500).nextDeadline).toBeUndefined();
      const resumed=buildWorkspaceRail([ticket()]);
      expect(group(railHandoff(held.state,resumed,held.rail,contexts,500).rail)).toBe('agent');
    }
  });
  it('keeps first-load Empty immediate and cancels on context, parent, removal, or fresh attention',()=>{
    const empty=buildWorkspaceRail([ticket('a',{ticket_status:'empty'})]);
    expect(group(railHandoff(emptyRailHandoffState(),empty,empty,contexts,0).rail)).toBe('empty');
    const agent=buildWorkspaceRail([ticket()]);
    const first=railHandoff(emptyRailHandoffState(),agent,agent,contexts,0);
    const held=railHandoff(first.state,empty,agent,contexts,1);
    for(const context of ['new-runtime','new-scope','new-window']) {
      expect(group(railHandoff(held.state,empty,held.rail,new Map([['chief',context]]),2).rail)).toBe('empty');
    }
    for(const extra of [{awaiting_approval:true},{awaiting_answer:true},{assigned:true},{agent_state:'errored' as const},{waiting_to_closeout:true},{stage:'done'},{blocked:true},{is_backlogged:true}]) {
      const incoming=buildWorkspaceRail([ticket('a',{ticket_status:'empty',...extra})]);
      expect(railHandoff(held.state,incoming,held.rail,contexts,2).retained.size).toBe(0);
    }
    expect(railHandoff(held.state,buildWorkspaceRail([]),held.rail,contexts,2).rail.chiefGroups).toEqual([]);
    const manager={id:'m',title:'m',created_at:0,updated_at:0,settled:false,project_id:undefined,project:undefined,recap:'',recap_preview:'',tickets:[ticket('a',{ticket_status:'empty'})],conversation_id:null,awaiting_reply:false,awaiting_answer:false,awaiting_approval:false,awaiting_agent_approval:false,assigned:false,agent_state:'idle' as const};
    const moved=railHandoff(held.state,buildWorkspaceRail([],[manager]),held.rail,new Map([...contexts,['m','manager-window']]),2);
    expect(moved.rail.items[0].groups[0].key).toBe('empty');
  });
  it('releases retained placement during hover and supersedes an in-flight destination',async()=>{
    vi.useFakeTimers();
    try {
      let presented:WorkspaceRailPresentation={rail:buildWorkspaceRail([]),settledOpen:false};
      const motion=createRailMotionController(()=>null,()=>presented,value=>presented=value);
      const owner=createRailHandoffController(()=>presented.rail,(value:WorkspaceRailPresentation,urgent)=>motion.update(value,urgent));
      const settle = async () => {for(let i=0;i<12;i++) await tick();};
      const update=(extra:Partial<WorkspaceTicket>={})=>owner.update({rail:buildWorkspaceRail([ticket('a',extra)]),settledOpen:false},contexts);
      update(); await settle();
      motion.freeze(true);
      update({ticket_status:'empty',title:'Fresh Empty',stage:'needs_consequences'});
      expect(group(presented.rail)).toBe('agent');
      expect(presented.rail.chiefGroups[0].cards[0].title).toBe('Fresh Empty');
      vi.advanceTimersByTime(1000);
      expect(group(presented.rail)).toBe('empty');
      motion.freeze(false); await settle();
      update({awaiting_approval:true}); await settle();
      motion.freeze(true); update({ticket_status:'empty'});
      expect(group(presented.rail)).toBe('awaiting_approval');
      update({awaiting_answer:true,title:'New answer'});
      expect(group(presented.rail)).toBe('awaiting_answer');
      expect(presented.rail.chiefGroups[0].cards[0].title).toBe('New answer');
      motion.freeze(false); await settle();
      update(); await settle();
      update({ticket_status:'empty'});
      owner.update({rail:buildWorkspaceRail([]),settledOpen:false},contexts);
      expect(presented.rail.chiefGroups).toEqual([]);
      await settle();
      expect(presented.rail.chiefGroups).toEqual([]);
      owner.dispose(); motion.dispose();
    } finally {vi.useRealTimers();}
  });
  it('expires independent rows without a publication, and disposal cancels the timer',()=>{
    vi.useFakeTimers();
    try {
      let shown=buildWorkspaceRail([]);
      const publications:boolean[]=[];
      const owner=createRailHandoffController(()=>shown,(value:{rail:typeof shown},expired)=>{shown=value.rail;publications.push(expired)});
      owner.update({rail:buildWorkspaceRail([ticket('a'),ticket('b')])},contexts);
      owner.update({rail:buildWorkspaceRail([ticket('a',{ticket_status:'empty'}),ticket('b')])},contexts);
      vi.advanceTimersByTime(400);
      owner.update({rail:buildWorkspaceRail([ticket('a',{ticket_status:'empty'}),ticket('b',{ticket_status:'empty'})])},contexts);
      vi.advanceTimersByTime(600);
      expect(group(shown,'a')).toBe('empty');
      expect(group(shown,'b')).toBe('agent');
      vi.advanceTimersByTime(400);
      expect(group(shown,'b')).toBe('empty');
      expect(publications.filter(Boolean)).toHaveLength(2);
      owner.update({rail:buildWorkspaceRail([ticket()])},contexts);
      owner.update({rail:buildWorkspaceRail([ticket('a',{ticket_status:'empty'})])},contexts);
      owner.dispose(); const count=publications.length;
      vi.advanceTimersByTime(2000);
      expect(publications).toHaveLength(count);
    } finally {vi.useRealTimers();}
  });
});
