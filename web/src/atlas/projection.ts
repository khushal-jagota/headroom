import type { BoardCard } from "../lib/types";
import type { AtlasBuilding, AtlasDistrict, AtlasInputs, AtlasWorld } from "./contracts";

const DISTRICTS: readonly AtlasDistrict[] = ["northbank", "rivergate", "southfield"];
const BUILDING_SLOTS = [
  [18, 30], [43, 24], [69, 34], [28, 62], [56, 58], [78, 70], [13, 76], [87, 17]
] as const;

function stableNumber(value: string): number {
  let hash = 2166136261;
  for (const character of value) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function ticketCards(inputs: AtlasInputs): BoardCard[] {
  return inputs.board.columns.flatMap((column) =>
    column.cards.map((card) => ({ ...card, stage: column.stage }))
  );
}

function projectBuilding(project: AtlasInputs["projects"][number], cards: BoardCard[], index: number): AtlasBuilding {
  const projectCards = cards.filter((card) => card.project_id === project.id);
  const active = projectCards.find((card) => !card.is_done) ?? projectCards[0] ?? null;
  const slot = BUILDING_SLOTS[index % BUILDING_SLOTS.length];
  const cycle = Math.floor(index / BUILDING_SLOTS.length);
  const spread = stableNumber(project.id) % 7;
  return {
    id: project.id,
    label: project.name,
    summary: project.summary || "No project summary yet.",
    district: DISTRICTS[index % DISTRICTS.length],
    x: Math.min(90, slot[0] + cycle * 6 + spread),
    y: Math.min(84, slot[1] + cycle * 5),
    activeTicketId: active ? String(active.id) : null,
    activeTicketTitle: active ? String(active.title) : null,
    href: active ? `#/workspace/${encodeURIComponent(String(active.id))}` : "#/backlog",
    state: active && !active.is_done ? "active" : "quiet"
  };
}

export function atlasWorld(inputs: AtlasInputs): AtlasWorld {
  const cards = ticketCards(inputs);
  const buildings = [...inputs.projects]
    .sort((left, right) => left.id.localeCompare(right.id))
    .map((project, index) => projectBuilding(project, cards, index));
  const agents = [
    {
      id: "chief-of-staff",
      label: inputs.workers.chief_of_staff.label,
      role: "Chief of staff",
      x: 50,
      y: 47,
      href: "#/workspace/chief-of-staff"
    },
    ...inputs.workers.workers.map((worker, index) => ({
      id: worker.worker_type,
      label: worker.label,
      role: worker.worker_type,
      x: 16 + ((index * 27) % 70),
      y: 16 + ((index * 19) % 68),
      href: `#/config/workers/${encodeURIComponent(worker.worker_type)}`
    }))
  ];
  const alerts = inputs.review.items.map((item) => ({
    id: `${item.review_item_type}:${item.ticket_id}`,
    label: item.title,
    ticketId: item.ticket_id,
    href: `#/ticket/${encodeURIComponent(item.ticket_id)}`
  }));
  const activeJobCount = cards.filter((card) => !card.is_done && card.ticket_status !== "empty").length;
  return {
    buildings,
    agents,
    alerts,
    activeJobCount,
    crewCount: agents.length,
    approvalCount: alerts.length
  };
}
