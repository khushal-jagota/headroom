import { WORK_ITEM_TICKET_GROUPS, type WorkItemTicketFacts } from './workItemPresentation';
import { stabilizeRailGroups, type WorkspaceRail, type WorkspaceTicketGroup } from './workspaceRail';

export const EMPTY_HANDOFF_GRACE_MS = 1_000;
type Placement = { parent: string; group: string; context: string };
type Hold = Placement & { deadline: number };
export type RailHandoffState = { observed: Map<string, Placement>; holds: Map<string, Hold> };
export const emptyRailHandoffState = (): RailHandoffState => ({observed:new Map(), holds:new Map()});
const eligible = new Set(['agent', 'awaiting_approval', 'status_awaiting_approval']);
function parents(rail: WorkspaceRail): [string, WorkspaceTicketGroup[]][] {
  return [['chief', rail.chiefGroups], ...rail.items.map(item => [item.id, item.groups] as [string, WorkspaceTicketGroup[]]),
    ...rail.settledItems.map(item => [item.id, item.groups] as [string, WorkspaceTicketGroup[]])];
}

/** Retain placement only. Observations, including expired Empty, prevent deadline renewal. */
export function railHandoff(state: RailHandoffState, incoming: WorkspaceRail, presented: WorkspaceRail,
  contexts: ReadonlyMap<string, string>, now: number) {
  const observed = new Map<string, Placement>();
  const holds = new Map<string, Hold>();
  const retained = new Set<string>();
  const previousGroups = new Map(parents(presented));
  let nextDeadline: number | undefined;
  const groupsFor = (parent: string, groups: WorkspaceTicketGroup[]) => {
    const context = contexts.get(parent) ?? '';
    const oldGroups = previousGroups.get(parent) ?? [];
    const destinations = new Map<string, { group: WorkspaceTicketGroup; cards: WorkItemTicketFacts[] }>();
    for (const group of groups) for (const ticket of group.cards) {
      const placement = {parent, group:group.key, context};
      observed.set(ticket.id, placement);
      if (group.key !== 'empty' || !context) continue;
      const prior = state.observed.get(ticket.id);
      const oldGroup = oldGroups.find(old => old.cards.some(row => row.id === ticket.id));
      const existing = state.holds.get(ticket.id);
      const hold = existing?.context === context && existing.parent === parent ? existing
        : prior?.context === context && prior.parent === parent && eligible.has(prior.group)
          && oldGroup && eligible.has(oldGroup.key) ? {...placement, group:oldGroup.key, deadline:now + EMPTY_HANDOFF_GRACE_MS} : undefined;
      if (!hold) continue;
      // Keep expired holds until the Ticket leaves Empty, even while motion still shows its old group.
      holds.set(ticket.id, hold);
      if (now >= hold.deadline) continue;
      retained.add(ticket.id);
      nextDeadline = Math.min(nextDeadline ?? Infinity, hold.deadline);
      let destination = destinations.get(hold.group);
      if (!destination) {
        const definition = WORK_ITEM_TICKET_GROUPS.find(group => group.key === hold.group)!;
        destination = {group:{key:definition.key,label:definition.label,defaultCollapsed:definition.quiet,cards:[]}, cards:[]}; destinations.set(hold.group, destination);
      }
      destination.cards.push(ticket);
    }
    const result = groups.map(group => ({...group, cards:group.cards.filter(ticket => !retained.has(ticket.id))}));
    for (const [key, destination] of destinations) {
      const current = result.find(group => group.key === key);
      if (current) current.cards.push(...destination.cards);
      else result.push({...destination.group, cards:destination.cards});
    }
    // Follow the shared category order, including a category absent from the incoming answer.
    const order = ['awaiting_approval','awaiting_answer','assigned','errored','agent','waiting_to_closeout','status_awaiting_approval','empty'];
    return stabilizeRailGroups(result.filter(group => group.cards.length).sort((a,b) => order.indexOf(a.key)-order.indexOf(b.key)), oldGroups);
  };
  const rail = {...incoming, chiefGroups:groupsFor('chief',incoming.chiefGroups),
    items:incoming.items.map(item => ({...item,groups:groupsFor(item.id,item.groups)})),
    settledItems:incoming.settledItems.map(item => ({...item,groups:groupsFor(item.id,item.groups)}))};
  return {rail, state:{observed,holds}, retained, nextDeadline};
}

/** One timer uses the newest input and never waits for another server publication. */
export function createRailHandoffController<T extends {rail:WorkspaceRail}>(read: () => WorkspaceRail,
  publish: (value:T, expired:boolean) => void) {
  let state = emptyRailHandoffState();
  let latest: {value:T; contexts:ReadonlyMap<string,string>} | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let disposed = false;
  let retained = new Set<string>();
  function update(value:T, contexts:ReadonlyMap<string,string>, expired=false) {
    if (disposed) return;
    clearTimeout(timer);
    latest = {value,contexts};
    const result = railHandoff(state,value.rail,read(),contexts,performance.now());
    const released = [...retained].some(id => !result.retained.has(id));
    state = result.state; retained = result.retained;
    publish({...value,rail:result.rail, handoffRetained:result.retained},expired || released);
    if (result.nextDeadline !== undefined) timer = setTimeout(() => {
      if (latest) update(latest.value,latest.contexts,true);
    },Math.max(0,result.nextDeadline-performance.now()));
  }
  return {update, dispose() {disposed=true;clearTimeout(timer);latest=undefined;state=emptyRailHandoffState();}};
}
