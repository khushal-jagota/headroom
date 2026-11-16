/** Exact ordered text that Panels supplies for one explicit worker step. */
import type { Ticket } from '../../tickets/contracts.ts';
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
  },
): WorkerStepPrompt {
  const field = gating_field(options.worker_type_definition, ticket.stage) ?? 'the next step';
  const ownership = stage_ownership_mode(ticket.stage, options);
  const ownership_wire = ownership ?? 'terminal';
  let instruction =
    field === 'consequences' && ownership !== 'user'
      ? `Work ticket ${ticket.id} — ${ticket.title}. It is at Stage '${ticket.stage}'; ` +
        'complete the authorized Consequences work and submit its verified report. ' +
        'The ceiling holder reviews the Consequences report. ' +
        'Preserve actual blockers and required human work. ' +
        'Do not repeat completed side effects. ' +
        `Stage owner: ${ownership_wire}.`
      : ownership === 'user'
        ? `Work ticket ${ticket.id} — ${ticket.title}. It is at Stage '${ticket.stage}'; ` +
          `open the collaborative discussion for the '${field}' field. ` +
          'Ask bounded questions or resume the Stage conversation, and do not file a ' +
          'proposal until the discussion has enough shared understanding. ' +
          (field === 'consequences'
            ? 'Once the required human work is complete, the Consequences report waits for the ceiling holder to review it. '
            : 'Once filed, the answer settles below the ceiling and waits for approval at or beyond it. ') +
          `Stage owner: ${ownership_wire}.`
        : `Work ticket ${ticket.id} — ${ticket.title}. It is at Stage '${ticket.stage}'; ` +
          `take the next step and propose the '${field}' field for approval. ` +
          `Stage owner: ${ownership_wire}.`;
  instruction += ` Read your Ticket first: ${READ_YOUR_TICKET_COMMAND}. ${FOLLOW_YOUR_WORKFLOW_GUIDE}`;
  return new WorkerStepPrompt({
    inputs: [
      {
        name: 'stage instruction',
        text: instruction,
        quoted: false,
      },
      ...(field === 'consequences'
        ? [
            {
              name: 'Consequences work and recovery',
              text: options.worker_type_definition.stages.find(
                (stage) => stage.id === ticket.stage,
              )!.instructions,
              quoted: true,
            },
          ]
        : []),
    ],
  });
}
function _render_worker_step_input(item: WorkerStepInput): string {
  return item.quoted ? `[${item.name}]\n${item.text}\n[/${item.name}]` : item.text;
}

export function worker_step_memory_loss_prompt(ticket: Ticket): string {
  return (
    MEMORY_LOSS_NOTICE +
    '\n\n[Ticket workflow guidance]\n' +
    render_workflow_guide(ticket.workflow) +
    '\n[/Ticket workflow guidance]'
  );
}

export function mid_step_memory_loss_prompt(ticket: Ticket): string {
  return (
    `You are part-way through a step on ticket ${ticket.id} — ${ticket.title}, at Stage '${ticket.stage}'. ` +
    worker_step_memory_loss_prompt(ticket)
  );
}
