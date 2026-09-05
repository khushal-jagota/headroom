import type {
  Priority,
  ScheduleCadence,
  SchedulePlacementMode,
  ScheduledTask
} from "./types";

export type ScheduledTaskForm = {
  enabled: boolean;
  cadence: ScheduleCadence;
  local_time: string;
  title: string;
  worker_type: string;
  kickoff_note: string;
  priority: Priority;
  deadline: string;
  project_id: string | null;
  placement_mode: SchedulePlacementMode;
  sprint_item_id: string | null;
  sprint_id: string | null;
  employee_backend: string;
  employee_launch_model: string;
  blocked_by_ticket_ids: string;
};

export function blankScheduledTaskForm(workerType = ""): ScheduledTaskForm {
  return {
    enabled: true,
    cadence: "every_planning_day",
    local_time: "09:00",
    title: "",
    worker_type: workerType,
    kickoff_note: "",
    priority: "P3",
    deadline: "",
    project_id: null,
    placement_mode: "current_sprint",
    sprint_item_id: null,
    sprint_id: null,
    employee_backend: "",
    employee_launch_model: "",
    blocked_by_ticket_ids: ""
  };
}

export function scheduledTaskFormFromSchedule(schedule: ScheduledTask): ScheduledTaskForm {
  return {
    enabled: schedule.enabled,
    cadence: schedule.cadence,
    local_time: schedule.local_time,
    title: schedule.title,
    worker_type: schedule.worker_type,
    kickoff_note: schedule.kickoff_note,
    priority: schedule.priority,
    deadline: schedule.deadline || "",
    project_id: schedule.project_id,
    placement_mode: schedule.placement_mode,
    sprint_item_id: schedule.sprint_item_id,
    sprint_id: schedule.sprint_id,
    employee_backend: schedule.employee_backend || "",
    employee_launch_model: schedule.employee_launch_model || "",
    blocked_by_ticket_ids: schedule.blocked_by_ticket_ids.join(", ")
  };
}

function blockedByIds(raw: string): string[] {
  return raw
    .split(/[\s,]+/)
    .map((value) => value.trim())
    .filter(Boolean);
}

export function scheduledTaskPayload(form: ScheduledTaskForm): Record<string, unknown> {
  return {
    enabled: form.enabled,
    cadence: form.cadence,
    local_time: form.local_time,
    title: form.title.trim(),
    worker_type: form.worker_type,
    kickoff_note: form.kickoff_note,
    priority: form.priority,
    deadline: form.deadline.trim() || null,
    project_id: form.project_id,
    placement_mode: form.placement_mode,
    sprint_item_id: form.sprint_item_id,
    sprint_id: form.placement_mode === "backlog" ? null : form.sprint_id,
    employee_backend: form.employee_backend.trim() || null,
    employee_launch_model: form.employee_launch_model.trim() || null,
    blocked_by_ticket_ids: blockedByIds(form.blocked_by_ticket_ids)
  };
}

export function scheduledTaskFormError(form: ScheduledTaskForm): string | null {
  if (!form.title.trim()) return "Give the scheduled Ticket a title.";
  if (!form.worker_type) return "Choose a Worker type.";
  if (!form.local_time) return "Choose a local time.";
  return null;
}
