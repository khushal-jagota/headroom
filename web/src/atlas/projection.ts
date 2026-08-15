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

function projectBuilding(project: AtlasInputs["projects"][number], cards: BoardCard[]): AtlasBuilding {
  const projectCards = cards.filter((card) => card.project_id === project.id);
  const current = projectCards.find((card) => card.agent_working) ?? projectCards.find((card) => !card.is_done) ?? null;
  const projectNumber = stableNumber(project.id);
  const slot = BUILDING_SLOTS[projectNumber % BUILDING_SLOTS.length];
  return {
    id: project.id,
    label: project.name,
    summary: project.summary || "No project summary yet.",
    district: DISTRICTS[Math.floor(projectNumber / BUILDING_SLOTS.length) % DISTRICTS.length],
    x: Math.min(90, slot[0] + ((projectNumber >>> 8) % 7)),
    y: Math.min(84, slot[1] + ((projectNumber >>> 16) % 7)),
    currentTicketId: current ? String(current.id) : null,
    currentTicketTitle: current ? String(current.title) : null,
    href: current ? `#/workspace/${encodeURIComponent(String(current.id))}` : "#/backlog",
    state: current?.agent_working ? "active" : "quiet"
  };
}

export function atlasWorld(inputs: AtlasInputs): AtlasWorld {
  const cards = ticketCards(inputs);
  const buildings = [...inputs.projects]
    .sort((left, right) => left.id.localeCompare(right.id))
    .map((project) => projectBuilding(project, cards));
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
  return {
    buildings,
    agents,
    alerts,
    runningWorkerCount: inputs.review.running_worker_count,
    crewCount: agents.length,
    approvalCount: alerts.length
  };
}
