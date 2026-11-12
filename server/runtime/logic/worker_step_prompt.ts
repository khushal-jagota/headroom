/** Exact ordered text that Panels supplies for one explicit worker step. */
import type { Ticket } from '../../tickets/contracts.ts';
import type { PendingRevisionFeedback } from '../../tickets/revision_feedback.ts';
import { stage_ownership_mode, gating_field } from '../../tickets/logic/machine.ts';
import type { WorkflowDefinition } from '../../worker_types/contracts.ts';
import { render_workflow_guide } from '../../worker_types/guidance.ts';
export const READ_YOUR_TICKET_COMMAND = 'panels worker my-ticket brief';
export const FOLLOW_YOUR_WORKFLOW_GUIDE =
  'Follow this Ticket’s workflow guide. ' +
  'Read the full guide again with panels worker my-ticket workflow if needed.';
export const MEMORY_LOSS_NOTICE =
  'Your context was compacted, so the conversation you were working from is gone. ' +
  `Re-read your Ticket with ${READ_YOUR_TICKET_COMMAND}, re-load the panels-worker skill, ` +
  'follow this Ticket’s workflow guide, then continue this step.';
export interface WorkerStepInput {
  name: string;
  text: string;
  quoted: boolean;
}
export class WorkerStepPrompt {
  readonly inputs: readonly WorkerStepInput[];
  constructor(values: { inputs: readonly WorkerStepInput[] }) {
    this.inputs = values.inputs;
  }
  get model_text(): string {
    return this.inputs.map(_render_worker_step_input).join('\n\n');
  }
}
export function compose_worker_step_prompt(
  ticket: Ticket,
  options: {
    worker_type_definition: WorkflowDefinition;
    revision_feedback: PendingRevisionFeedback | null;
    memory_was_lost: boolean;
  },
): WorkerStepPrompt {
  const field = gating_field(options.worker_type_definition, ticket.stage) ?? 'the next step';
  const ownership = stage_ownership_mode(ticket.stage, options);
  const ownership_wire = ownership ?? 'terminal';
  let instruction =
    ownership === 'user'
      ? `Work ticket ${ticket.id} — ${ticket.title}. It is at Stage '${ticket.stage}'; ` +
        `open the collaborative discussion for the '${field}' field. ` +
        'Ask bounded questions or resume the Stage conversation, and do not file a ' +
        'proposal until the discussion has enough shared understanding. ' +
        'Once filed, the answer settles below the ceiling and waits for approval ' +
        `at or beyond it. Stage owner: ${ownership_wire}.`
      : `Work ticket ${ticket.id} — ${ticket.title}. It is at Stage '${ticket.stage}'; ` +
        `take the next step and propose the '${field}' field for approval. ` +
        `Stage owner: ${ownership_wire}.`;
  instruction += ` Read your Ticket first: ${READ_YOUR_TICKET_COMMAND}. ${FOLLOW_YOUR_WORKFLOW_GUIDE}`;
  const inputs: WorkerStepInput[] = [];
  if (options.memory_was_lost) {
    inputs.push({
      name: 'memory loss',
      text: MEMORY_LOSS_NOTICE,
      quoted: false,
    });
    inputs.push({
      name: 'Ticket workflow guidance',
      text: render_workflow_guide(ticket.workflow),
      quoted: true,
    });
  }
  inputs.push({
    name: 'stage instruction',
    text: instruction,
    quoted: false,
  });
  if (options.revision_feedback !== null) {
    inputs.push({
      name: 'Ticket revision feedback',
      text: options.revision_feedback.text,
      quoted: true,
    });
  }
  return new WorkerStepPrompt({ inputs });
}
function _render_worker_step_input(item: WorkerStepInput): string {
  return item.quoted ? `[${item.name}]\n${item.text}\n[/${item.name}]` : item.text;
}
