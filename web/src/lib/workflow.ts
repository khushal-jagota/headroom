import type { WorkerTypeRecord } from './types';

/** The editable portion shared by a preset and a Ticket's own workflow. */
export type WorkflowRecord = Pick<WorkerTypeRecord, 'stages' | 'fields' | 'guidance'> & { label?: string };

export function copyWorkflow(value: WorkflowRecord): WorkflowRecord {
  return {
    label: value.label ?? 'Custom workflow',
    stages: value.stages.map(stage => ({ ...stage, instructions: stage.instructions ?? '' })),
    fields: value.fields.map(field => ({ ...field })),
    guidance: value.guidance ?? ''
  };
}


export function workflowError(value: WorkflowRecord): string | null {
  if (!value.stages.length) return 'Add at least one stage.';
  if (new Set(value.stages.map(stage => stage.id)).size !== value.stages.length) return 'Each stage needs a different identifier.';
  if (new Set(value.fields.map(field => field.id)).size !== value.fields.length) return 'Each result needs a different identifier.';
  for (const stage of value.stages) {
    if (!stage.label.trim() || !stage.id.trim()) return 'Give each stage a name.';
    if (!stage.is_terminal && (!stage.gating_field || !value.fields.some(field => field.id === stage.gating_field && field.label.trim()))) return `Give ${stage.label} a result.`;
  }
  return null;
}
