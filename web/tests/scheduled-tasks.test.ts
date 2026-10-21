import { describe, expect, it } from "vitest";
import { blankScheduledTaskForm, scheduledTaskFormFromSchedule, scheduledTaskIsRetired, scheduledTaskPayload } from "../src/lib/scheduledTasks";
import type { ScheduledTask } from "../src/lib/types";

const schedule: ScheduledTask = {
  id: "schedule", enabled: false, cadence: "daily", local_time: "09:00", title: "Ordinary work",
  worker_type: "coding", kickoff_note: "Keep the context", deadline: null,
  project_id: "project", sprint_item_id: "item",
  orchestrator: { kind: "sprint_item", id: "item" }, employee_backend: null,
  employee_launch_model: null, blocked_by_ticket_ids: [], created_at: 1, updated_at: 1
};

describe("scheduled Ticket retirement form", () => {
  it("creates daily work with an explicit Chief assignment and no retired fields", () => {
    const form = blankScheduledTaskForm("coding");
    form.title = " Work ";
    const payload = scheduledTaskPayload(form);
    expect(payload).toEqual({ enabled: true, cadence: "daily", local_time: "09:00", title: "Work",
      worker_type: "coding", kickoff_note: "", deadline: null, project_id: null,
      orchestrator: { kind: "chief", id: "chief" }, employee_backend: null,
      employee_launch_model: null, blocked_by_ticket_ids: [] });
  });
  it("preserves explicit item assignment through ordinary schedule edits", () => {
    const form = scheduledTaskFormFromSchedule(schedule);
    expect(scheduledTaskPayload(form).orchestrator).toEqual({ kind: "sprint_item", id: "item" });
    expect(scheduledTaskPayload(form)).not.toHaveProperty("sprint_item_id");
    expect(scheduledTaskPayload(form)).not.toHaveProperty("placement_mode");
    expect(scheduledTaskPayload(form)).not.toHaveProperty("sprint_id");
  });
  it("reads older assignment rows without modifying their source record", () => {
    const historical = { ...schedule, orchestrator: undefined, cadence: "every_planning_day" as const };
    const form = scheduledTaskFormFromSchedule(historical);
    expect(form.orchestrator).toEqual({ kind: "sprint_item", id: "item" });
    expect(historical.cadence).toBe("every_planning_day");
    expect(scheduledTaskIsRetired(historical)).toBe(true);
  });
  it("matches backend retirement protection for calendar cadences and retired Worker types", () => {
    expect(scheduledTaskIsRetired(schedule)).toBe(false);
    for (const cadence of ["every_planning_day", "current_sprint_day_four", "current_sprint_final_day"] as const)
      expect(scheduledTaskIsRetired({ ...schedule, cadence })).toBe(true);
    for (const worker_type of ["personal", "planning-day", "planning-midday-check", "planning-sprint"])
      expect(scheduledTaskIsRetired({ ...schedule, worker_type })).toBe(true);
  });
});
