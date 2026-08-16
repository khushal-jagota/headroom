// The board, read as a world.
//
// Atlas shows the same facts as every other screen. This turns the board and the
// review queue into the shape the world is built from — projects as islands,
// Sprint Items as pads, Tickets as the workers on them — and computes nothing the
// board does not already say. Progress is where a Ticket's stage sits in the
// board's own column order, not a new measure.
//
// It is a plain function so it can be tested without a browser or a scene.

import type { BoardCard, BoardResponse, ReviewItem, ReviewResponse } from "../types";
import { createSlotAllocator, type SlotBook } from "./slots";

// What a worker is doing, in the world's own vocabulary. It is a reading of the
// stage the Ticket is on, and it names an animation, not a new signal.
export type AtlasActivity = "survey" | "measure" | "carve" | "polish";

export type AtlasTicket = {
  id: string;
  title: string;
  project: string | null;
  done: boolean;
  working: boolean;
  needsMe: boolean;
  trouble: boolean;
  troubleKind: "error" | "blocked" | null;
  stage: string;
  stageLabel: string;
  gating: string;
  activity: AtlasActivity;
  workerType: string;
  // How far through the work stands, 0 to 1. Used for the structure's state and
  // for the item's progress; never shown as a number.
  frac: number;
  conversationId: string | null;
  ticketStatus: string;
  hasProposal: boolean;
  slot: number;
};

export type AtlasItem = {
  id: string;
  title: string;
  slot: number;
  tickets: AtlasTicket[];
  doneCount: number;
  total: number;
  progress: number;
  allDone: boolean;
  // Which trade most of the crew belongs to. It picks the structure's kind.
  dominantType: string;
  supervisorWorking: boolean;
  supervisorNeedsMe: boolean;
};

export type AtlasProject = {
  id: string;
  name: string;
  slot: number;
  items: AtlasItem[];
};

export type AtlasWorld = {
  projects: AtlasProject[];
  // The review queue, in the order the human would walk it.
  review: ReviewItem[];
  runningWorkerCount: number;
};

// A Ticket with no Sprint Item still belongs somewhere. It joins one pad per
// project, so nothing falls out of the world.
export const UNSCHEDULED_ITEM_TITLE = "Unscheduled";

export function activityForStage(stage: string): AtlasActivity {
  if (!stage) return "carve";
  if (/success|approach|kickoff/.test(stage)) return "survey";
  if (/plan/.test(stage)) return "measure";
  if (/closeout/.test(stage)) return "polish";
  return "carve";
}

function fractionFor(card: BoardCard, columnIndex: number, columnCount: number): number {
  if (card.is_done) return 1;
  const span = Math.max(1, columnCount - 1);
  return 0.25 + 0.6 * (columnIndex / span);
}

export function buildAtlasWorld(
  board: BoardResponse | undefined,
  review: ReviewResponse | undefined,
  book: SlotBook
): { world: AtlasWorld; book: SlotBook; slotsGrew: boolean } {
  const allocator = createSlotAllocator(book);
  const columns = board?.columns ?? [];
  const columnCount = Math.max(2, columns.length);
  const columnIndexByStage = new Map<string, number>();
  columns.forEach((column, index) => columnIndexByStage.set(column.stage, index));

  type Building = { project: AtlasProject; items: Map<string, AtlasItem> };
  const building = new Map<string, Building>();

  for (const column of columns) {
    for (const card of column.cards) {
      if (card.is_dropped) continue;
      const projectId = card.project_id || "project-unknown";
      let entry = building.get(projectId);
      if (!entry) {
        entry = {
          project: {
            id: projectId,
            name: card.project || "—",
            slot: allocator.slotFor("projects", projectId),
            items: []
          },
          items: new Map()
        };
        building.set(projectId, entry);
      }
      const itemId = card.sprint_item_id || `loose-${projectId}`;
      let item = entry.items.get(itemId);
      if (!item) {
        item = {
          id: itemId,
          title: card.sprint_item_title || UNSCHEDULED_ITEM_TITLE,
          slot: allocator.slotFor("items", itemId),
          tickets: [],
          doneCount: 0,
          total: 0,
          progress: 0,
          allDone: false,
          dominantType: "default",
          supervisorWorking: false,
          supervisorNeedsMe: false
        };
        entry.items.set(itemId, item);
      }
      const columnIndex = columnIndexByStage.get(card.stage) ?? Math.floor(columnCount / 2);
      item.tickets.push({
        id: card.id,
        title: card.title,
        project: card.project,
        done: !!card.is_done,
        working: !!card.agent_working,
        needsMe: !!card.needs_me,
        trouble: !!(card.backend_error || card.blocked),
        troubleKind: card.backend_error ? "error" : card.blocked ? "blocked" : null,
        stage: card.stage || "",
        stageLabel: card.stage_label || "",
        gating: card.gating_field_label || "",
        activity: activityForStage(card.stage),
        workerType: card.worker_type || "default",
        frac: fractionFor(card, columnIndex, columnCount),
        conversationId: card.conversation_id || null,
        ticketStatus: card.ticket_status || "",
        hasProposal: !!card.has_pending_proposal,
        slot: allocator.slotFor("tickets", card.id)
      });
    }
  }

  // The board carries each Sprint Item's own supervisor signals beside the cards.
  const supervisorById = new Map((board?.sprint_items ?? []).map((item) => [item.id, item]));

  const projects: AtlasProject[] = [];
  for (const entry of building.values()) {
    const items = [...entry.items.values()];
    for (const item of items) {
      const supervisor = supervisorById.get(item.id);
      item.supervisorWorking = !!supervisor?.agent_working;
      item.supervisorNeedsMe = !!supervisor?.needs_me;
      item.tickets.sort((a, b) => a.slot - b.slot);
      item.total = item.tickets.length;
      item.doneCount = item.tickets.filter((ticket) => ticket.done).length;
      item.progress =
        item.tickets.reduce((sum, ticket) => sum + ticket.frac, 0) / Math.max(1, item.total);
      item.allDone = item.total > 0 && item.doneCount === item.total;
      const counts = new Map<string, number>();
      for (const ticket of item.tickets) {
        counts.set(ticket.workerType, (counts.get(ticket.workerType) || 0) + 1);
      }
      item.dominantType =
        [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || "default";
    }
    items.sort((a, b) => a.slot - b.slot);
    entry.project.items = items;
    projects.push(entry.project);
  }
  projects.sort((a, b) => a.slot - b.slot);

  return {
    world: {
      projects,
      review: review?.items ?? [],
      runningWorkerCount: review?.running_worker_count ?? 0
    },
    book: allocator.book,
    slotsGrew: allocator.grew
  };
}

export function findAtlasTicket(world: AtlasWorld, ticketId: string): AtlasTicket | null {
  for (const project of world.projects) {
    for (const item of project.items) {
      const ticket = item.tickets.find((candidate) => candidate.id === ticketId);
      if (ticket) return ticket;
    }
  }
  return null;
}

export function findAtlasItem(world: AtlasWorld, itemId: string): AtlasItem | null {
  for (const project of world.projects) {
    const item = project.items.find((candidate) => candidate.id === itemId);
    if (item) return item;
  }
  return null;
}

// What changed between two readings of the world. The world speaks these as
// bubbles over the figure they belong to; no message text is invented.
export type AtlasHappening = {
  ticketId: string;
  itemId: string;
  text: string;
  tone: "done" | "trouble" | "needs" | "stage";
};

export function happeningsBetween(before: AtlasWorld | null, after: AtlasWorld): AtlasHappening[] {
  if (!before) return [];
  const happenings: AtlasHappening[] = [];
  for (const project of after.projects) {
    for (const item of project.items) {
      for (const ticket of item.tickets) {
        const was = findAtlasTicket(before, ticket.id);
        if (!was) continue; // a Ticket that has just arrived announces nothing
        if (!was.done && ticket.done) {
          happenings.push({ ticketId: ticket.id, itemId: item.id, text: "Finished!", tone: "done" });
        } else if (!was.trouble && ticket.trouble) {
          happenings.push({ ticketId: ticket.id, itemId: item.id, text: "Hit trouble", tone: "trouble" });
        } else if (!was.needsMe && ticket.needsMe) {
          happenings.push({ ticketId: ticket.id, itemId: item.id, text: "Needs you", tone: "needs" });
        } else if (was.stage !== ticket.stage) {
          happenings.push({
            ticketId: ticket.id,
            itemId: item.id,
            text: `Moved to ${ticket.stageLabel || ticket.stage}`,
            tone: "stage"
          });
        }
      }
    }
  }
  return happenings;
}
