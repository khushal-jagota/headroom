/** `panels ticket schema`: the keys the Ticket create and edit routes accept, offline. */
import {
  TICKET_CREATE_KEYS,
  TICKET_EDIT_KEYS,
  type TicketCreateKey,
  type TicketEditKey,
} from '../tickets/logic/body_keys.ts';

const CREATE: Record<TicketCreateKey, string> = {
  worker_type: 'Worker type id from panels worker-type list. Required unless workflow is given.',
  workflow: 'Custom stages for this Ticket instead of the Worker type preset.',
  employee_backend: 'Backend that runs the Worker. Default: the Worker type setting.',
  employee_launch_model: 'Model on that backend.',
  employee_launch_reasoning_effort: 'Reasoning effort, for a model that takes one, or null.',
  locale: 'Legacy name for the destination: vps or macos. Prefer machine_id.',
  machine_id: 'Destination from panels machine list. Default: the home.',
  title: 'Required. A short title.',
  brief: 'Required. What the Ticket is for, in Markdown.',
  deadline: 'A date (YYYY-MM-DD) or ISO week (YYYY-Www), or null.',
  project: 'Project name. Use project or project_id.',
  project_id: 'Project id.',
  manager:
    'Who supervises it: {"kind":"manager","id":"MANAGER_ID"} or {"kind":"chief","id":"chief"}. Default: the Chief.',
  blocked_by_ticket_ids: 'Ticket ids this Ticket waits for.',
  ceiling: 'The Stage where proposals start waiting for a person. Default: the brief.',
  ceiling_holder:
    'Who decides at the ceiling: {"kind":"owner","id":"owner"}, the Chief, or a Manager. Default: the creator.',
};
const EDIT: Record<TicketEditKey, string> = {
  title: 'A new title.',
  deadline: 'A date (YYYY-MM-DD) or ISO week (YYYY-Www), or null to clear it.',
  project: 'Project name, or null.',
  project_id: 'Project id, or null.',
  manager: 'Move it: {"kind":"manager","id":"MANAGER_ID"} or {"kind":"chief","id":"chief"}.',
  recap: 'Replace the recap text.',
  field_values:
    'Stage result fields to replace: {"FIELD_ID": "text"}. Editing clears the approval.',
  ceiling: 'A new ceiling Stage.',
  ceiling_holder: 'Who decides at the ceiling: owner, the Chief, or a Manager, as {"kind","id"}.',
};
const EXAMPLES: Record<'create' | 'edit', Record<string, unknown>> = {
  create: {
    worker_type: 'coding',
    title: 'Add the export button',
    brief: 'Add an export button to the report page.',
    project: 'Panels',
    manager: { kind: 'manager', id: 'MANAGER_ID' },
  },
  edit: { title: 'Add the CSV export button', deadline: '2026-12-01' },
};
export function ticket_schema_document(operations: string[]) {
  return Object.fromEntries(
    operations.map((operation) => {
      const keys = operation === 'create' ? TICKET_CREATE_KEYS : TICKET_EDIT_KEYS;
      const meaning: Record<string, string> = operation === 'create' ? CREATE : EDIT;
      return [
        operation,
        {
          command: `panels ticket ${operation}${operation === 'edit' ? ' TICKET_ID' : ''} < FILE`,
          keys: Object.fromEntries(keys.map((key) => [key, meaning[key]])),
          example: EXAMPLES[operation as 'create' | 'edit'],
        },
      ];
    }),
  );
}
export function ticket_schema_text(document: ReturnType<typeof ticket_schema_document>): string {
  const sections = Object.entries(document).map(([operation, entry]) => {
    const width = Math.max(...Object.keys(entry.keys).map((key) => key.length)) + 2;
    return [
      `ticket ${operation} takes one JSON object: ${entry.command}`,
      ...Object.entries(entry.keys).map(([key, meaning]) => `  ${key.padEnd(width)}${meaning}`),
      'Example:',
      `  ${JSON.stringify(entry.example)}`,
    ].join('\n');
  });
  return sections.join('\n\n');
}
