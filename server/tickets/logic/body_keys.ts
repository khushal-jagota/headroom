/** The keys the Ticket create and edit routes accept. The routes and `panels ticket schema` read these lists. */
export const TICKET_CREATE_KEYS = [
  'worker_type',
  'workflow',
  'employee_backend',
  'employee_launch_model',
  'employee_launch_reasoning_effort',
  'locale',
  'machine_id',
  'title',
  'brief',
  'deadline',
  'project',
  'project_id',
  'manager',
  'blocked_by_ticket_ids',
  'ceiling',
  'ceiling_holder',
] as const;
export const TICKET_EDIT_KEYS = [
  'title',
  'deadline',
  'project',
  'project_id',
  'manager',
  'recap',
  'field_values',
  'ceiling',
  'ceiling_holder',
] as const;
export type TicketCreateKey = (typeof TICKET_CREATE_KEYS)[number];
export type TicketEditKey = (typeof TICKET_EDIT_KEYS)[number];
