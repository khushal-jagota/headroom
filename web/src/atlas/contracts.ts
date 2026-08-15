import type { BoardResponse, ProjectSummary, ReviewResponse, WorkersResponse } from "../lib/types";

export type AtlasDistrict = "northbank" | "rivergate" | "southfield";

export type AtlasBuilding = {
  id: string;
  label: string;
  summary: string;
  district: AtlasDistrict;
  x: number;
  y: number;
  currentTicketId: string | null;
  currentTicketTitle: string | null;
  href: string;
  state: "active" | "quiet";
};

export type AtlasAgent = {
  id: string;
  label: string;
  role: string;
  x: number;
  y: number;
  href: string;
};

export type AtlasAlert = {
  id: string;
  label: string;
  ticketId: string;
  href: string;
};

export type AtlasWorld = {
  buildings: AtlasBuilding[];
  agents: AtlasAgent[];
  alerts: AtlasAlert[];
  runningWorkerCount: number;
  crewCount: number;
  approvalCount: number;
};

export type AtlasInputs = {
  projects: ProjectSummary[];
  board: BoardResponse;
  workers: WorkersResponse;
  review: ReviewResponse;
};
