import { describe, expect, it } from "vitest";
import {
  blankScheduledTaskForm,
  scheduledTaskFormError,
  scheduledTaskFormFromSchedule,
  scheduledTaskPayload
} from "../src/lib/scheduledTasks";
import type { ScheduledTask } from "../src/lib/types";

const schedule: ScheduledTask = {
  id: "schedule_demo",
  enabled: false,
  cadence: "current_sprint_final_day",
  local_time: "17:00",
  title: "Review the sprint",
  worker_type: "planning-sprint",
  kickoff_note: "Bring the current sprint evidence.",
  priority: "P2",
  deadline: "2026-08-02",
  project_id: "project_panels",
  placement_mode: "sprint_item",
  sprint_item_id: "si_demo",
  employee_backend: "codex",
  employee_launch_model: "gpt-5.6-luna",
  blocked_by_ticket_ids: ["t_blocker", "t_other"],
  created_at: 1,
  updated_at: 2
};

describe("scheduled task form mapping", () => {
  it("round-trips server configuration into an editable form payload", () => {
    const form = scheduledTaskFormFromSchedule(schedule);

    expect(form).toMatchObject({
      enabled: false,
      cadence: "current_sprint_final_day",
      local_time: "17:00",
      title: "Review the sprint",
      placement_mode: "sprint_item",
      sprint_item_id: "si_demo",
      blocked_by_ticket_ids: "t_blocker, t_other"
    });
    expect(scheduledTaskPayload(form)).toEqual({
      enabled: false,
      cadence: "current_sprint_final_day",
      local_time: "17:00",
      title: "Review the sprint",
      worker_type: "planning-sprint",
      kickoff_note: "Bring the current sprint evidence.",
      priority: "P2",
      deadline: "2026-08-02",
      project_id: "project_panels",
      placement_mode: "sprint_item",
      sprint_item_id: "si_demo",
      employee_backend: "codex",
      employee_launch_model: "gpt-5.6-luna",
      blocked_by_ticket_ids: ["t_blocker", "t_other"]
    });
  });

  it("clears sprint placement and nullable overrides for a new schedule", () => {
    const form = blankScheduledTaskForm("coding");
    form.title = "Plan the day";
    form.local_time = "05:05";
    form.project_id = "project_panels";
    form.placement_mode = "backlog";
    form.sprint_item_id = "stale_item";
    form.employee_backend = "  ";
    form.blocked_by_ticket_ids = " t_one,\n t_two ";

    expect(scheduledTaskPayload(form)).toMatchObject({
      project_id: "project_panels",
      placement_mode: "backlog",
      sprint_item_id: null,
      employee_backend: null,
      blocked_by_ticket_ids: ["t_one", "t_two"]
    });
  });

  it("round-trips the current sprint day four cadence", () => {
    const form = scheduledTaskFormFromSchedule({
      ...schedule,
      cadence: "current_sprint_day_four"
    });

    expect(scheduledTaskPayload(form).cadence).toBe("current_sprint_day_four");
  });

  it("reports the fields the screen cannot submit without", () => {
    const form = blankScheduledTaskForm();
    expect(scheduledTaskFormError(form)).toBe("Give the scheduled Ticket a title.");

    form.title = "Scheduled work";
    expect(scheduledTaskFormError(form)).toBe("Choose a Worker type.");

    form.worker_type = "coding";
    form.placement_mode = "sprint_item";
    expect(scheduledTaskFormError(form)).toBe("Choose a sprint item for this placement.");
  });
});
