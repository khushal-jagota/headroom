import type {
  Principal,
  ScheduleCadence,
  ScheduledTask
} from "./types";

export type ScheduledTaskForm = {
  enabled: boolean;
  orchestrator: Principal;
  local_time: string;
  title: string;
  worker_type: string;
  kickoff_note: string;
  deadline: string;
  project_id: string | null;
  employee_backend: string;
  employee_launch_model: string;
  blocked_by_ticket_ids: string;
};

export function blankScheduledTaskForm(workerType = ""): ScheduledTaskForm {
  return {
    enabled: true,
    orchestrator: { kind: "chief", id: "chief" },
    local_time: "09:00",
    title: "",
    worker_type: workerType,
    kickoff_note: "",
    deadline: "",
    project_id: null,
    employee_backend: "",
    employee_launch_model: "",
    blocked_by_ticket_ids: ""
  };
}

export function scheduledTaskFormFromSchedule(schedule: ScheduledTask): ScheduledTaskForm {
  return {
    enabled: schedule.enabled,
    orchestrator: schedule.orchestrator ?? (schedule.sprint_item_id
      ? { kind: "sprint_item", id: schedule.sprint_item_id }
      : { kind: "chief", id: "chief" }),
    local_time: schedule.local_time,
    title: schedule.title,
    worker_type: schedule.worker_type,
    kickoff_note: schedule.kickoff_note,
    deadline: schedule.deadline || "",
    project_id: schedule.project_id,
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
    cadence: "daily",
    orchestrator: form.orchestrator,
    local_time: form.local_time,
    title: form.title.trim(),
    worker_type: form.worker_type,
    kickoff_note: form.kickoff_note,
    deadline: form.deadline.trim() || null,
    project_id: form.project_id,
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

/** Historical calendar schedules remain records, never editable daily templates. */
export function scheduledTaskIsRetired(schedule: ScheduledTask): boolean {
  return schedule.cadence !== "daily" ||
    ["personal", "planning-day", "planning-midday-check", "planning-sprint"].includes(schedule.worker_type);
}

export function scheduledTaskCadenceLabel(cadence: ScheduleCadence): string {
  return {
    daily: "Every day",
    every_planning_day: "Every planning day",
    current_sprint_day_four: "Current sprint day four",
    current_sprint_final_day: "Current sprint final day"
  }[cadence];
}
