import { shortMonthDayLabel } from "./dates";
import type { FieldStageVisualState } from "./ui";
import type { WorkAttention } from "./types";
import { primaryWorkAttention } from "./workAttentionPresentation";

export type FeedbackNote = {
  id: string;
  text: string;
  page_address: string | null;
  page_label: string | null;
  state: "open" | "handled";
  ticket_id: string | null;
  created_at: number;
  updated_at: number;
  handled_at: number | null;
};

export type FeedbackTicket = WorkAttention & {
  id: string;
  title: string;
  stage: string;
  ticket_status: string;
};

export type FeedbackHandledGroup = {
  ticket: FeedbackTicket | null;
  notes: FeedbackNote[];
};

export type FeedbackResponse = {
  open_count: number;
  open: FeedbackNote[];
  handled_groups: FeedbackHandledGroup[];
};

export type FeedbackCountResponse = {
  open_count: number;
};

export type FeedbackPageContext = {
  address: string;
  label: string;
};

const SCREEN_LABELS: Record<string, string> = {
  day: "Home",
  review: "Review",
  workspace: "Workspace",
  sprint: "Sprint",
  backlog: "Backlog",
  ideas: "Ideas",
  feedback: "Feedback",
  config: "Config",
  backends: "Backends",
  notifications: "Notifications",
  "scheduled-tasks": "Scheduled tasks",
  preview: "Preview"
};

function decoded(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

export function feedbackPageContext(hash: string, detailTitle = ""): FeedbackPageContext {
  const address = hash && hash !== "#" ? hash : "#/day";
  const path = address.replace(/^#/, "").split("?")[0];
  const segments = path.split("/").filter(Boolean);
  const screen = segments[0] || "day";
  const title = detailTitle.trim();

  if (screen === "workspace") {
    if (segments[1] === "item" && segments.length === 3) {
      return { address, label: title ? `Sprint Item · ${title}` : "Sprint Item" };
    }
    if ((segments[1] === "item" && segments.length === 4) ||
        (segments.length === 2 && segments[1] !== "chief-of-staff")) {
      return { address, label: title ? `Ticket · ${title}` : "Ticket" };
    }
  }
  if (screen === "sprint" && new URLSearchParams(address.split("?")[1] || "").has("item")) {
    return { address, label: title ? `Sprint Item · ${title}` : "Sprint Item" };
  }
  return { address, label: SCREEN_LABELS[screen] || decoded(screen) };
}

// Page context is navigation inside Panels, never an arbitrary URL. Treat stored
// values as untrusted even though the capture UI only writes the current hash.
export function feedbackPageHref(address: string | null): string | null {
  return address && /^#\/[A-Za-z0-9][^\s]*$/.test(address) ? address : null;
}

export function feedbackRelativeTime(value: number, now = new Date()): string {
  const date = new Date(value * 1000);
  if (Number.isNaN(date.getTime())) return "";
  const elapsed = Math.max(0, now.getTime() - date.getTime());
  const minutes = Math.floor(elapsed / 60_000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} min ago`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return shortMonthDayLabel(date);
}

export function feedbackTicketStageState(ticket: FeedbackTicket): FieldStageVisualState {
  if (ticket.stage === "done") return "completed";
  if (ticket.ticket_status === "errored" || ticket.ticket_status === "blocked") return "errored";
  const attention = primaryWorkAttention(ticket);
  if (attention === "awaiting_approval") return "current-awaiting-approval";
  if (attention === "assigned") return "current-assigned";
  if (attention === "awaiting_reply") return "needs-me";
  if (ticket.agent_state === "working") return "current-running";
  if (ticket.agent_state === "errored") return "errored";
  if (ticket.stage === "needs_consequences") return "current-waiting";
  return "upcoming";
}

export function feedbackTicketStateLabel(ticket: FeedbackTicket): string {
  if (ticket.stage === "done") return "Done";
  if (ticket.ticket_status === "blocked") return "Blocked";
  const attention = primaryWorkAttention(ticket);
  if (attention === "awaiting_approval") return "Awaiting approval";
  if (attention === "assigned") return "Assigned";
  if (attention === "awaiting_reply") return "Needs you";
  if (ticket.agent_state === "working") return "Running";
  if (ticket.agent_state === "errored") return "Errored";
  const labels: Record<string, string> = { errored: "Errored", blocked: "Blocked" };
  if (ticket.ticket_status === "empty" && ticket.stage === "needs_consequences") {
    return "Waiting for closeout";
  }
  const stage = ticket.stage.replace(/_/g, " ");
  return labels[ticket.ticket_status] || `${stage.charAt(0).toUpperCase()}${stage.slice(1)}`;
}
